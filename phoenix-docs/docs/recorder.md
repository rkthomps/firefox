# The recorder

The recorder writes down what SpiderMonkey's baseline IC pipeline does, so that the Lean model
can be run on the same inputs and compared. So far it records **generator runs**: every time a
baseline IC's stub generator runs, its inputs, what it decided, and the CacheIR it wrote. This is
step 1 of goal 1b in [feasibility.md](feasibility.md).

How to build it and run it, on one script or on all of jit-tests, is in the
[README](../README.md). This file is how it works and what it writes.

## What `--enable-phoenix` changes

The recorder is compiled in only with `--enable-phoenix`, which defines `JS_PHOENIX`. Without
the flag, `phoenix-dev` builds the same code as `phoenix` (see "Changes to Firefox" in
[CLAUDE.md](../CLAUDE.md)). With it, the recorder still does nothing unless `PHOENIX_RECORD` is
set.

- `js/moz.configure`: the option and the `JS_PHOENIX` define.
- `jit/PhoenixRecorder.h`, `jit/PhoenixRecorder.cpp` (in `jit/moz.build`, under
  `CONFIG["JS_PHOENIX"]`): the recorder.
- `jit/BaselineIC.cpp`, `TryAttachStub`: the hook. It's the one place where every baseline
  generator that goes through the generic path is built and run, and where the decision and
  whether the stub was attached are both known.
- `jit/CacheIRSpewer.{h,cpp}`: the AOT serializer (`SpewCacheIROpsAsAOT`) is compiled under
  `ENABLE_JS_AOT_ICS || JS_PHOENIX`. Two upstream bugs in it, which the recorder hit, are fixed
  by `[BUG FIX]` commits, with reports in [bug-fixes/](../bug-fixes/):
  [0001](../bug-fixes/0001-aot-spewer-typeofeq-operand.md) and
  [0002](../bug-fixes/0002-aot-spewer-immutable-view-kind.md).

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

### Deduplicated records

`phoenix-tools/corpus.py dedupe` writes `unique.jsonl` next to the records: one line per distinct
generator record, i.e. records equal apart from `pid` and `loc`. Each is the record without
those two fields, plus `count` (how many records it stands for), `locs` (their `file:line:column`
locations) and `tests` (the tests they came from, from the process records).

## Not recorded yet

- Generators built outside `TryAttachStub`: `SetProp` and `SetElem` (whose decision can be
  `Deferred`), and `Call` and `SpreadCall`. They need hooks in their own fallbacks.
- Ion's ICs (`IonIC.cpp`). Ion doesn't run baseline stubs, and the runs use `--no-ion`.
- Compiled stub code, and the interpreter operations (goals 1a, 2 and 4).
