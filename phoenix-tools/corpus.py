#!/usr/bin/env python3
"""Merge and deduplicate the generator records of a PHOENIX_RECORD run.

  corpus.py dedupe RECORDS_DIR
      Merge RECORDS_DIR/<pid>.jsonl into RECORDS_DIR/unique.jsonl, one line per
      distinct generator record, with how many times it occurred, where (the
      IC's source locations) and in which tests. Then print a summary by kind,
      stub name and the tags of the Value inputs.

  corpus.py tests RECORDS_DIR --stub NAME [--tags TAG,TAG,...]
      Print the tests whose records have stub name NAME (and, with --tags,
      those tags for the Value inputs, in order), from unique.jsonl.

Records are identical when they're equal apart from "pid" and "loc". A
record's test is the script its process ran, from the process record that
starts each run's lines: the last "-f" or "--module" argument.
"""

import argparse
import collections
import json
import pathlib
import sys

PER_RUN_KEYS = ("pid", "loc")


def value_tags(record):
    return tuple(a["tag"] for a in record["args"] if a["type"] == "Value")


def loc_string(loc):
    return f"{loc['file']}:{loc['line']}:{loc['column']}"


def test_of(argv):
    if "--" in argv:
        argv = argv[: argv.index("--")]
    test = None
    for flag, value in zip(argv, argv[1:]):
        if flag in ("-f", "--module"):
            test = value
    return test


def dedupe(records_dir):
    unique = {}
    total = 0
    for path in sorted(records_dir.glob("[0-9]*.jsonl")):
        test = None
        with path.open() as f:
            for lineno, line in enumerate(f, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"{path}:{lineno}: skipping: {e}", file=sys.stderr)
                    continue
                if record["record"] == "process":
                    test = test_of(record["argv"])
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

    print(f"{total} records, {len(unique)} distinct, written to {out_path}")
    summary = collections.Counter()
    for entry in unique.values():
        name = entry["stubName"] or f"({entry['decision']})"
        summary[(entry["kind"], name, value_tags(entry))] += entry["count"]
    for (kind, name, tags), count in sorted(summary.items()):
        print(f"{count:10}  {kind:20} {name:40} {','.join(tags)}")


def tests(records_dir, stub, tags):
    found = set()
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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("dedupe")
    p.add_argument("records_dir", type=pathlib.Path)
    p = sub.add_parser("tests")
    p.add_argument("records_dir", type=pathlib.Path)
    p.add_argument("--stub", required=True)
    p.add_argument("--tags", type=lambda s: tuple(s.split(",")))
    args = parser.parse_args()

    if args.command == "dedupe":
        dedupe(args.records_dir)
    else:
        tests(args.records_dir, args.stub, args.tags)


if __name__ == "__main__":
    main()
