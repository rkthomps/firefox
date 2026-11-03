# Phoenix: testing the Lean CacheIR model against SpiderMonkey

This directory documents the infrastructure on `phoenix-dev` that tests the Lean model in
`../cachet-lean` against the real SpiderMonkey.

- [GOAL.md](GOAL.md): the four testing goals.
- [FEASIBILITY.md](FEASIBILITY.md): how feasible each goal is, the plan, and where it stands.
- [RECORDER.md](RECORDER.md): the recorder in detail: what it changes in SpiderMonkey, and the
  record format.
- [bug-fixes/](bug-fixes/): reports for the upstream bugs fixed on `phoenix-dev`, one per
  `[BUG FIX]` commit.
- [FUTURE_WORK.md](FUTURE_WORK.md): reminders.
- [CLAUDE.md](CLAUDE.md): instructions for agents, including the rules for changing Firefox and
  for the `phoenix` and `phoenix-dev` branches. `phoenix-tools/check-stack.py --worktree` checks
  that the branches follow them.

The rest of this file is how to record what SpiderMonkey's IC stub generators do.

## Building the recorder

The recorder is compiled in only with `--enable-phoenix`. The JS-shell objdir `obj-js-x64` has
it, from `mozconfig-js-x64` at the top of the worktree:

```
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --enable-phoenix
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-x64
```

`mozconfig*` files are gitignored, so recreate this one in a new worktree. Build with:

```
MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build
```

The shell is `obj-js-x64/dist/bin/js`. `record-jit-tests.sh` (below) builds it for you.

## Recording jit-tests

```
phoenix-tools/record-jit-tests.sh [--no-build] LABEL [KINDS] [-- JIT_TEST_ARGS...]
```

- `LABEL` names the run, which goes in `../phoenix-runs/<date>-LABEL`. Set `PHOENIX_RUNS` to put
  runs somewhere else. A label that already has a run that day is refused, not overwritten.
- `KINDS` is a comma-separated list of `CacheKind`s to record, e.g. `UnaryArith`. Without it,
  every kind is recorded, even if `PHOENIX_RECORD_KINDS` is set in your shell.
- Arguments after `--` go to `jit_test.py`, e.g. test paths to run only those tests.
- `--no-build` skips building `obj-js-x64`. Building is the default: when nothing has changed it
  takes about a second, and it guards against recording with a stale shell.

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

What each field means is in [RECORDER.md](RECORDER.md).
