# Phoenix: testing the Lean CacheIR model against SpiderMonkey

This directory documents the infrastructure on `phoenix-dev` that tests the Lean model in
`../cachet-lean` against the real SpiderMonkey.

- [GOAL.md](GOAL.md): the four testing goals.
- [TODO.md](TODO.md): work deferred for later.
- [CLAUDE.md](CLAUDE.md): instructions for agents: where things are, the rules for changing
  Firefox, and how to keep these docs up to date.
- [bug-fixes/](bug-fixes/): reports for the upstream bugs fixed on `phoenix`, one per
  `[BUG FIX]` commit.
- [docs/](docs/), how things work:
  - [feasibility.md](docs/feasibility.md): how feasible each goal is, the plan, and where it
    stands;
  - [recorder.md](docs/recorder.md): how the recorder works, and its record format;
  - [substitution.md](docs/substitution.md): replacing stub code with Lean's, the replay table's
    format, and what the Lean driver has to produce;
  - [usr-index.md](docs/usr-index.md): the USR index of the reference's C++ entities, for
    `cachet-lean`'s provenance checker;
  - [branches.md](docs/branches.md): the `phoenix` and `phoenix-dev` branches, the reference
    checkout, how to add a bug fix, and how to move to a new pin;
  - [spidermonkey.md](docs/spidermonkey.md): the SpiderMonkey code this effort hooks into, the
    builds, and their pitfalls.

The rest of this file is what the tools need, how to record what SpiderMonkey's IC stub
generators and compiler do, how to run the tests on replacement stub code, and how to index the
reference's C++.

## Dependencies

- **Firefox's toolchain**, from `./mach bootstrap`, in `~/.mozbuild`: the clang that builds the
  shells, and its `libclang.so`, which the USR index uses.
- **Python 3.10 or later**, for the tools in `phoenix-tools/`. They're scripts, run as files, and
  use only the standard library, plus libclang's Python bindings, which are vendored in
  `phoenix-tools/third_party/clang/` (see [docs/usr-index.md](docs/usr-index.md)).
- **uv** (<https://docs.astral.sh/uv/>), to type-check the tools. `phoenix-tools/pyproject.toml`
  has pyright as a dev dependency, which `uv.lock` pins. In `phoenix-tools/`, `uv run pyright`
  makes a `.venv` (gitignored), installs pyright in it, and checks every tool in pyright's
  standard mode. On its first run, pyright's PyPI wrapper downloads a Node.js of its own into
  its cache (Node 26.13.0 on 2026-11-05), since Mozilla's isn't on the PATH.

## Building the recorder

The recorder is compiled in only with `--enable-phoenix`. The JS-shell objdir `obj-js-x64` has
it, from `mozconfig-js-x64` at the top of the worktree. `mozconfig*` files are gitignored; this
one's contents, to recreate it in a new worktree, are in [docs/spidermonkey.md](docs/spidermonkey.md).
Build with:

```
MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build
```

The shell is `obj-js-x64/dist/bin/js`. `record-jit-tests.sh` (below) builds it for you.

## Recording jit-tests

```
phoenix-tools/record-jit-tests.sh [--no-build] [--replay TABLE] LABEL [KINDS] [-- JIT_TEST_ARGS...]
```

- `LABEL` names the run, which goes in `../phoenix-runs/<date>-LABEL`. Set `PHOENIX_RUNS` to put
  runs somewhere else. A label that already has a run that day is refused, not overwritten.
- `KINDS` is a comma-separated list of `CacheKind`s to record, e.g. `UnaryArith`. Without it,
  every kind is recorded, even if `PHOENIX_RECORD_KINDS` is set in your shell.
- Arguments after `--` go to `jit_test.py`, e.g. test paths to run only those tests.
- `--no-build` skips building `obj-js-x64`. Building is the default: when nothing has changed it
  takes about a second, and it guards against recording with a stale shell.
- `--replay TABLE` runs the tests with the stub code in `TABLE` (see "Running on replacement stub
  code" below).

For example:

```
phoenix-tools/record-jit-tests.sh unaryarith UnaryArith                      # all jit-tests
phoenix-tools/record-jit-tests.sh everything                                 # every kind
phoenix-tools/record-jit-tests.sh quick UnaryArith -- basic/testBitwise.js   # one test
```

The script:

1. builds `obj-js-x64`, and checks that it was built with `--enable-phoenix`;
2. tries `KINDS` on a one-line script, so a misspelt kind stops it at once instead of crashing
   every test;
3. runs jit-tests with `--no-ion --baseline-eager`. All of jit-tests takes a few minutes on this
   machine;
4. dedupes the records, and prints the test results, the failed tests, and how many records it
   got.

All of jit-tests fails two tests, `baseline/blinterp-trial-inlining.js` and
`cacheir/bound-function-trial-inlining.js`. They need Ion, so `--no-ion` makes them fail, with or
without the recorder.

A run's directory holds:

| File | What it is |
|---|---|
| `records/<pid>.jsonl` | the records of one shell process: its command line, then one line per generator run |
| `records/unique.jsonl` | the distinct records, each with its count, source locations and tests |
| `summary.txt` | the number of records, by kind, stub name and input types |
| `jit-test.log`, `main_raw.log` | `jit_test.py`'s output |
| `failures.txt` | the tests that failed |
| `build.log` | the build's output, unless `--no-build` |
| `command.txt` | the command that made the run |
| `rev.txt` | the git revision, and the files with uncommitted changes |

## Recording one script

Set `PHOENIX_RECORD` to an existing directory and run the shell:

```
mkdir -p /tmp/rec
PHOENIX_RECORD=/tmp/rec PHOENIX_RECORD_KINDS=UnaryArith \
  obj-js-x64/dist/bin/js --no-ion --baseline-eager phoenix-tools/tests/bitnot.js
```

- `PHOENIX_RECORD=<dir>` turns the recorder on. Each process writes `<dir>/<pid>.jsonl`.
- `PHOENIX_RECORD_KINDS` limits it to some `CacheKind`s. Without it, every kind is recorded.
- `--baseline-eager` makes ICs attach from the first execution. Without it, a short script never
  reaches an IC, and nothing is recorded.
- `--no-ion` keeps hot code on the baseline stubs.
- A directory that doesn't exist, or a kind that doesn't, crashes the shell at the first IC, so
  a mistake can't pass for an empty run.

## Looking at the records

```
python3 phoenix-tools/corpus.py dedupe RUN/records
python3 phoenix-tools/corpus.py tests RUN/records --stub UnaryArith.BitwiseBitNot --tags int32,int32
```

- `dedupe` writes `unique.jsonl` and prints the summary. `record-jit-tests.sh` runs it already.
- `tests` lists the tests that produced a stub, optionally only for those input types (the
  `Value` inputs' tags, in order).

To look at individual records, use `jq`, e.g.:

```
jq -c 'select(.record=="generator") | [.args[1].tag, .stubName, .cacheir.aot]' RUN/records/*.jsonl
```

Generator records are the IC generators' runs, and compile records the compiler's code for each
CacheIR. What each field means is in [docs/recorder.md](docs/recorder.md).

## Running on replacement stub code

A replay table maps a kind and CacheIR to the machine code to use in place of SpiderMonkey's. The
Lean driver will write them; its format is in [docs/substitution.md](docs/substitution.md).

```
phoenix-tools/record-jit-tests.sh --replay TABLE LABEL [KINDS]
PHOENIX_REPLAY=TABLE obj-js-x64/dist/bin/js --no-ion --baseline-eager SCRIPT.js
```

- With `record-jit-tests.sh`, the table is copied into the run, and the run ends with a summary
  of what it replaced: `python3 phoenix-tools/corpus.py replay RUN/records`.
- With `PHOENIX_RECORD` set too, each compile record says whether its code was `substituted`,
  `notInTable` or `refused` (C++'s code needed relocations).
- A table that can't be read or parsed crashes the shell at the first stub compiled, with the
  file and line.

To test the mechanism, `phoenix-tools/replay-table.py` writes a table of SpiderMonkey's own code
from a run's compile records. Replaying it must change nothing. Restrict it to stubs that embed
no addresses of the process that compiled them, e.g. the int32 `~` stub:

```
python3 phoenix-tools/replay-table.py RUN/records --cacheir 0b00006c0100 > bitnot.table
```

## Indexing the reference's C++ by USR

```
(cd ../firefox-phoenix && MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build-backend -b CompileDB)
phoenix-tools/usr-index.py
```

The first command writes the compile database of `../firefox-phoenix/obj-js-x64`. Rerun it when
`phoenix` changes. The second parses every translation unit of that build, in about a minute, and
writes `../phoenix-index/x64.json`, which `cachet-lean`'s checker reads. Its options, its
dependencies and its format are in [docs/usr-index.md](docs/usr-index.md).
