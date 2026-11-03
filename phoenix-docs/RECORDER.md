# The recorder

The recorder writes down what SpiderMonkey's baseline IC pipeline does, so that the Lean model
can be run on the same inputs and compared. So far it records **generator runs**: every time a
baseline IC's stub generator runs, its inputs, what it decided, and the CacheIR it wrote. This is
step 1 of goal 1b in [FEASIBILITY.md](FEASIBILITY.md).

## Building it

The recorder is compiled in only with `--enable-phoenix`, which defines `JS_PHOENIX`. Without
the flag, `phoenix-dev` builds the same code as `phoenix`, plus the `[BUG FIX]` commits (see
"Changes to Firefox" in [CLAUDE.md](CLAUDE.md)). `mozconfig-js-x64` has the flag:

```
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --enable-phoenix
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-x64
```

`mozconfig*` files are gitignored, so this one lives only in the worktree.

What `--enable-phoenix` changes:

- `js/moz.configure`: the option and the `JS_PHOENIX` define.
- `jit/PhoenixRecorder.h`, `jit/PhoenixRecorder.cpp` (in `jit/moz.build`, under
  `CONFIG["JS_PHOENIX"]`): the recorder.
- `jit/BaselineIC.cpp`, `TryAttachStub`: the hook. It's the one place where every baseline
  generator that goes through the generic path is built and run, and where the decision and
  whether the stub was attached are both known.
- `jit/CacheIRSpewer.{h,cpp}`: the AOT serializer (`SpewCacheIROpsAsAOT`) is compiled under
  `ENABLE_JS_AOT_ICS || JS_PHOENIX`. Two upstream bugs in it, which the recorder hit, are fixed
  by `[BUG FIX]` commits, with reports in [bug-fixes/](bug-fixes/):
  [0001](bug-fixes/0001-aot-spewer-typeofeq-operand.md) and
  [0002](bug-fixes/0002-aot-spewer-immutable-view-kind.md).

## Running it

- `PHOENIX_RECORD=<dir>`: record into `<dir>`, which must exist. Each process that reaches an
  IC appends one JSON object per line to `<dir>/<pid>.jsonl`, starting with a process record.
  Without it, nothing is recorded.
- `PHOENIX_RECORD_KINDS=<kind>,<kind>,...`: record only these `CacheKind`s (e.g.
  `UnaryArith`). Without it, every kind is recorded.
- A directory that can't be opened, or a kind that doesn't exist, crashes the shell on the first
  IC, rather than silently recording nothing.
- ICs only run in baseline, so a script must reach the baseline interpreter: with
  `--baseline-eager` (or `--blinterp-eager`), or by running long enough. Run with `--no-ion` so
  that hot code stays on baseline stubs.

For all of jit-tests, use `phoenix-tools/record-jit-tests.sh`. It builds `obj-js-x64`, checks
the kinds, runs jit-tests with the flags below, and dedupes the records:

```
phoenix-tools/record-jit-tests.sh [--no-build] LABEL [KINDS] [-- JIT_TEST_ARGS...]
phoenix-tools/record-jit-tests.sh unaryarith UnaryArith
phoenix-tools/record-jit-tests.sh bitnot-only UnaryArith -- basic/testBitwise.js
```

The run goes in `../phoenix-runs/<date>-LABEL` (or `$PHOENIX_RUNS/<date>-LABEL`), with the
records, `unique.jsonl` and `summary.txt`, the jit-test log and `failures.txt`, the build log,
the command, and the revision and uncommitted changes it ran on. By hand, the run is:

```
RUN=../phoenix-runs/<date>-<label>; mkdir -p $RUN/records
PHOENIX_RECORD=$RUN/records PHOENIX_RECORD_KINDS=UnaryArith \
  python3 js/src/jit-test/jit_test.py obj-js-x64/dist/bin/js \
  --args="--no-ion --baseline-eager" --no-progress --format=automation -w $RUN/failures.txt
```

Runs go in `../phoenix-runs/`, outside every repository.

## What's recorded

Every line has `v`, the format's version, and `record`, its type.

### Process records

The first line of each process's records, written when the recorder starts:

```json
{"v":1,"record":"process","pid":3413522,"argv":["…/obj-js-x64/dist/bin/js","--no-ion",…,"-f","…/jit-test/tests/basic/bug1666856.js"]}
```

`argv` is the shell's command line, from `/proc/self/cmdline`. It names the test, which a
record's `loc` doesn't when the IC is in a library (`jit-test/lib/*.js`) or in code made by
`Function` or `eval`. For jit-tests, the test is the value of the last `-f` or `--module`.

### Generator records

One per generator run, e.g. for `~0`:

```json
{"v":1,"record":"generator","pid":3260920,"kind":"UnaryArith",
 "loc":{"file":"phoenix-tools/tests/bitnot.js","line":5,"column":10,"pcOffset":3,"op":"BitNot"},
 "state":{"mode":"Specialized","isFirstStub":true,"numOptimizedStubs":0},
 "args":[{"type":"JSOp","name":"BitNot","byte":20},
         {"type":"Value","bits":"0xfff8800000000000","tag":"int32"},
         {"type":"Value","bits":"0xfff88000ffffffff","tag":"int32"}],
 "decision":"Attach","attached":true,"stubName":"UnaryArith.BitwiseBitNot","writerFailed":false,
 "cacheir":{"aot":"UnaryArith, 1, 1, 2, JSVAL_TYPE_UNKNOWN, 0, , LASTUSED(1) , OP(GuardToInt32) ID(0) OP(Int32NotResult) ID(0) ",
            "bytes":"0b00006c0100","stubFields":[]}}
```

- `kind`: the generator's `CacheKind`.
- `loc`: where the IC is. `file` is the script's filename as the shell got it (jit-tests pass
  absolute paths), `line` and `column` are the IC's op's (1-origin column), `pcOffset` is its
  bytecode offset, and `op` its `JSOp`.
- `state`: the IC's `ICState` as the generator was constructed with it, which sets the
  generator's `mode_`, `isFirstStub_` and `numOptimizedStubs_`.
- `args`: the arguments `TryAttachStub` constructed the generator with after the IC state, in
  order: the generator's inputs. For `UnaryArithIRGenerator` they're `op`, `val` and `res`, the
  order of the Lean structure's fields. Each is one of:
  - `{"type":"Value","bits":…,"tag":…}`: `bits` is `Value::asRawBits()` as hex (x64 boxing), and
    `tag` its type, for filtering. For a GC thing, `bits` holds a pointer, meaningless without
    the heap.
  - `{"type":"JSOp","name":…,"byte":…}` and `{"type":"CacheKind","name":…}`.
  - `{"type":"bool","value":…}` and `{"type":"int","value":…}`.
  - `{"type":"unrecorded"}`: any other type (objects, names, frames, …).
- `decision`: the generator's `AttachDecision`.
- `attached`: whether the stub was added to the IC. `AttachBaselineCacheIRStub` can refuse a
  stub the generator decided to attach (a duplicate, too large, OOM).
- `stubName`: the generator's `stubName_`, set by `trackAttached`. It tells which `tryAttach*`
  attached the stub, e.g. `UnaryArith.BitwiseBitNot`. `null` if `trackAttached` wasn't called.
- `writerFailed`: whether the writer ran out of memory or grew too large.
- `cacheir`: `null` when the writer failed or is empty. Otherwise:
  - `aot`: `SpewCacheIROpsAsAOT`'s text without its final newline. The fields are, in order: the
    kind, `numInputOperands`, `numOperandIds`, `numInstructions`, `typeData`, `stubDataSize`, the
    stub fields' types, `operandLastUsed`, and the ops. (The comment above
    `SpewCacheIROpsAsAOT` lists `numOperandIds` before `numInputOperands`; the code prints them
    the other way round.) This is what the Lean serializer should print.
  - `bytes`: the CacheIR bytecode in hex, which with the kind is what the stub code cache is
    keyed on.
  - `stubFields`: each stub field's type and raw value, which the AOT text leaves out.

## Post-processing

`phoenix-tools/corpus.py`:

- `corpus.py dedupe RUN_DIR/records` merges the per-process files into `unique.jsonl`, one line
  per distinct generator record (equal apart from `pid` and `loc`), with a `count`, the `locs`
  it came from, and the `tests` that produced it. It prints a summary by kind, stub name and the
  `Value` inputs' tags.
- `corpus.py tests RUN_DIR/records --stub UnaryArith.BitwiseBitNot --tags int32,int32` lists the
  tests whose records have that stub name and those tags.

`phoenix-tools/tests/bitnot.js` is a targeted test: one `~` site that attaches the int32 stub,
runs it on edge values, then fails its guard with every other type.

## Not recorded yet

- Generators built outside `TryAttachStub`: `SetProp` and `SetElem` (whose decision can be
  `Deferred`), and `Call` and `SpreadCall`. They need hooks in their own fallbacks.
- Ion's ICs (`IonIC.cpp`). Ion doesn't run baseline stubs, and the runs use `--no-ion`.
- Compiled stub code, and the interpreter operations (goals 1a, 2 and 4).
