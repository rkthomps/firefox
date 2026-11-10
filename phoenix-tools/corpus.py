#!/usr/bin/env python3
"""Merge, deduplicate and summarize the records of a PHOENIX_RECORD run.

  corpus.py dedupe RECORDS_DIR
      Merge RECORDS_DIR/<pid>.jsonl into RECORDS_DIR/unique.jsonl, one line per
      distinct generator record, with how many times it occurred, where (the
      IC's source locations) and in which tests, and into
      RECORDS_DIR/unique-compile.jsonl, one line per distinct compile record
      (see below). Then print a summary by kind, stub name and the tags of the
      Value inputs.

  corpus.py tests RECORDS_DIR --stub NAME [--tags TAG,TAG,...]
      Print the tests whose records have stub name NAME (and, with --tags,
      those tags for the Value inputs, in order), from unique.jsonl.

  corpus.py replay RECORDS_DIR
      For a run with PHOENIX_REPLAY: count the compile records by what the
      replay table did with them, and list the stubs whose code was
      substituted, with how many tests compiled each.

Generator records are identical when they're equal apart from "pid" and
"loc". Compile records are identical when they're equal apart from "pid",
what belongs to the stub that triggered the compilation rather than to the
code ("stubName", and the writer's "typeData"), and their code's bytes, but
with the same length, and with the configuration of their process ("build"
and "jitOptions"). Embedded addresses make the bytes differ from process to
process, so each line has the first record's bytes, and the spans of bytes
where some other record differs from them. A record's test is the script its
process ran, from the process record that starts each run's lines: the last
"-f" or "--module" argument.
"""

import argparse
import collections
import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

# A record, or an entry of unique.jsonl, as parsed from JSON (phoenix-docs/docs/recorder.md).
Record = dict[str, Any]
# A stub's kind, its stub name and its CacheIR bytes in hex.
StubKey = tuple[str, str, str]

PER_RUN_KEYS = ("pid", "loc")


def value_tags(record: Record) -> tuple[str, ...]:
    return tuple(a["tag"] for a in record["args"] if a["type"] == "Value")


def loc_string(loc: dict[str, Any]) -> str:
    return f"{loc['file']}:{loc['line']}:{loc['column']}"


def test_of(argv: list[str]) -> str | None:
    if "--" in argv:
        argv = argv[: argv.index("--")]
    test = None
    for flag, value in zip(argv, argv[1:]):
        if flag in ("-f", "--module"):
            test = value
    return test


@dataclass
class CompileEntry:
    """The compile records of one line of unique-compile.jsonl."""

    body: Record
    first_code: str
    varying: int = 0  # the bits where some record's code differs from first_code
    codes: set[bytes] = field(default_factory=set[bytes])  # digests of the distinct codes
    count: int = 0
    stub_names: set[str | None] = field(default_factory=set[str | None])
    type_datas: set[int] = field(default_factory=set[int])
    tests: set[str] = field(default_factory=set[str])


def compile_body(record: Record, config: Record) -> Record:
    """A compile record's key: the record without what varies by stub or by process."""
    code = {k: v for k, v in record["code"].items() if k != "bytes"}
    code["length"] = len(record["code"]["bytes"]) // 2
    cacheir = record["cacheir"]
    if cacheir is not None:
        writer = {k: v for k, v in cacheir["writer"].items() if k != "typeData"}
        cacheir = {**cacheir, "writer": writer}
    return {
        "kind": record["kind"],
        "cacheir": cacheir,
        "replay": record["replay"],
        "code": code,
        "config": config,
    }


def byte_spans(bits: int, length: int) -> list[list[int]]:
    """The [start, end) spans of the bytes |bits| has a bit in, |bits| being |length| bytes."""
    spans: list[list[int]] = []
    for i, byte in enumerate(bits.to_bytes(length, "big")):
        if not byte:
            continue
        if spans and spans[-1][1] == i:
            spans[-1][1] = i + 1
        else:
            spans.append([i, i + 1])
    return spans


def compile_line(entry: CompileEntry) -> Record:
    body = entry.body
    length = body["code"]["length"]
    code = {k: v for k, v in body["code"].items() if k != "length"}
    code["bytes"] = entry.first_code
    code["varying"] = byte_spans(entry.varying, length)
    return {
        **body,
        "code": code,
        "stubNames": sorted(entry.stub_names, key=lambda name: (name is None, name or "")),
        "typeDatas": sorted(entry.type_datas),
        "distinctCodes": len(entry.codes),
        "count": entry.count,
        "tests": sorted(entry.tests),
    }


def dedupe(records_dir: pathlib.Path) -> None:
    unique: dict[str, Record] = {}
    unique_compile: dict[str, CompileEntry] = {}
    total = 0
    total_compile = 0
    for path in sorted(records_dir.glob("[0-9]*.jsonl")):
        test = None
        config: Record = {}
        with path.open() as f:
            for lineno, line in enumerate(f, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"{path}:{lineno}: skipping: {e}", file=sys.stderr)
                    continue
                if record["record"] == "process":
                    test = test_of(record["argv"])
                    config = {"build": record.get("build"), "jitOptions": record.get("jitOptions")}
                    continue
                if record["record"] == "compile":
                    total_compile += 1
                    body = compile_body(record, config)
                    code = record["code"]["bytes"]
                    entry_c = unique_compile.setdefault(
                        json.dumps(body, sort_keys=True), CompileEntry(body, code)
                    )
                    entry_c.count += 1
                    entry_c.codes.add(hashlib.blake2b(code.encode(), digest_size=16).digest())
                    if code != entry_c.first_code:
                        entry_c.varying |= int(code, 16) ^ int(entry_c.first_code, 16)
                    entry_c.stub_names.add(record["stubName"])
                    if record["cacheir"] is not None:
                        entry_c.type_datas.add(record["cacheir"]["writer"]["typeData"])
                    if test:
                        entry_c.tests.add(test)
                    continue
                if record["record"] != "generator":
                    continue
                total += 1
                body = {k: v for k, v in record.items() if k not in PER_RUN_KEYS}
                key = json.dumps(body, sort_keys=True)
                entry = unique.setdefault(
                    key, {**body, "count": 0, "locs": set(), "tests": set()}
                )
                entry["count"] += 1
                entry["locs"].add(loc_string(record["loc"]))
                if test:
                    entry["tests"].add(test)

    out_path = records_dir / "unique.jsonl"
    with out_path.open("w") as out:
        for entry in unique.values():
            entry = {**entry, "locs": sorted(entry["locs"]), "tests": sorted(entry["tests"])}
            out.write(json.dumps(entry) + "\n")
    compile_path = records_dir / "unique-compile.jsonl"
    with compile_path.open("w") as out:
        for entry_c in unique_compile.values():
            out.write(json.dumps(compile_line(entry_c)) + "\n")

    print(f"{total} records, {len(unique)} distinct, written to {out_path}")
    print(
        f"{total_compile} compile records, {len(unique_compile)} distinct, "
        f"written to {compile_path}"
    )
    summary: collections.Counter[tuple[str, str, tuple[str, ...]]] = collections.Counter()
    for entry in unique.values():
        name = entry["stubName"] or f"({entry['decision']})"
        summary[(entry["kind"], name, value_tags(entry))] += entry["count"]
    for (kind, name, tags), count in sorted(summary.items()):
        print(f"{count:10}  {kind:20} {name:40} {','.join(tags)}")


def replay(records_dir: pathlib.Path) -> None:
    results: collections.Counter[str] = collections.Counter()
    substituted: collections.Counter[StubKey] = collections.Counter()
    substituted_tests: collections.defaultdict[StubKey, set[str | None]] = collections.defaultdict(
        set
    )
    for path in sorted(records_dir.glob("[0-9]*.jsonl")):
        test = None
        with path.open() as f:
            for line in f:
                record = json.loads(line)
                if record["record"] == "process":
                    test = test_of(record["argv"])
                    continue
                if record["record"] != "compile" or record["replay"] is None:
                    continue
                results[record["replay"]] += 1
                if record["replay"] == "substituted":
                    key = (record["kind"], record["stubName"], record["cacheir"]["bytes"])
                    substituted[key] += 1
                    substituted_tests[key].add(test)
    total = sum(results.values())
    print(
        f"{total} stub compilations with a replay table: "
        + ", ".join(f"{count} {result}" for result, count in sorted(results.items()))
    )
    if substituted:
        print("Substituted (compilations, tests, kind, stub name, CacheIR):")
        for (kind, name, cacheir), count in sorted(substituted.items()):
            ntests = len(substituted_tests[(kind, name, cacheir)])
            print(f"{count:10} {ntests:6}  {kind:12} {name:30} {cacheir}")


def tests(records_dir: pathlib.Path, stub: str, tags: tuple[str, ...] | None) -> None:
    found: set[str] = set()
    with (records_dir / "unique.jsonl").open() as f:
        for line in f:
            entry = json.loads(line)
            if entry["stubName"] != stub:
                continue
            if tags is not None and value_tags(entry) != tags:
                continue
            found.update(entry["tests"])
    for test in sorted(found):
        print(test)


def tag_list(text: str) -> tuple[str, ...]:
    return tuple(text.split(","))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("dedupe")
    p.add_argument("records_dir", type=pathlib.Path)
    p = sub.add_parser("tests")
    p.add_argument("records_dir", type=pathlib.Path)
    p.add_argument("--stub", required=True)
    p.add_argument("--tags", type=tag_list)
    p = sub.add_parser("replay")
    p.add_argument("records_dir", type=pathlib.Path)
    args = parser.parse_args()

    if args.command == "dedupe":
        dedupe(args.records_dir)
    elif args.command == "tests":
        tests(args.records_dir, args.stub, args.tags)
    else:
        replay(args.records_dir)


if __name__ == "__main__":
    main()
