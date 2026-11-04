#!/usr/bin/env python3
"""Write a replay table (PHOENIX_REPLAY) of the code SpiderMonkey compiled.

  replay-table.py RECORDS_DIR [--kind KIND] [--stub NAME] [--cacheir HEX]...

Reads the compile records in RECORDS_DIR/<pid>.jsonl and prints a table
mapping each matching (kind, CacheIR) to the code the C++ compiler produced for
it. It's for testing substitution: replaying SpiderMonkey's own code must
change nothing. Lean's tables come from the Lean driver, in the same format
(phoenix-docs/docs/substitution.md).

C++'s code can embed absolute addresses of the process that compiled it, e.g.
of C++ functions it calls, and relocation tables don't record them all. So a
stub whose code differs between processes is left out, with a warning, and so
is one with relocations. A stub seen in only one process may still embed
addresses: restrict the table to stubs known not to, with --stub or --cacheir.
"""

import argparse
import collections
import json
import pathlib
import sys


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("records_dir", type=pathlib.Path)
    parser.add_argument("--kind", help="only this CacheKind")
    parser.add_argument("--stub", help="only stubs with this stub name")
    parser.add_argument(
        "--cacheir",
        action="append",
        help="only this CacheIR, in hex (repeatable)",
    )
    args = parser.parse_args()

    codes = collections.defaultdict(set)
    relocated = set()
    for path in sorted(args.records_dir.glob("[0-9]*.jsonl")):
        with path.open() as f:
            for line in f:
                record = json.loads(line)
                if record["record"] != "compile" or record["cacheir"] is None:
                    continue
                if args.kind and record["kind"] != args.kind:
                    continue
                if args.stub and record["stubName"] != args.stub:
                    continue
                cacheir = record["cacheir"]["bytes"]
                if args.cacheir and cacheir not in args.cacheir:
                    continue
                key = (record["kind"], cacheir)
                if record["code"]["relocationBytes"]:
                    relocated.add(key)
                codes[key].add(record["code"]["bytes"])

    print(f"# Replay table of SpiderMonkey's own code, from {args.records_dir}")
    written = 0
    for key in sorted(codes):
        kind, cacheir = key
        if key in relocated:
            print(f"skipping {kind} {cacheir}: its code has relocations", file=sys.stderr)
            continue
        if len(codes[key]) > 1:
            print(
                f"skipping {kind} {cacheir}: its code differs between processes",
                file=sys.stderr,
            )
            continue
        print(f"{kind} {cacheir} {next(iter(codes[key]))}")
        written += 1
    print(f"{written} entries", file=sys.stderr)


if __name__ == "__main__":
    main()
