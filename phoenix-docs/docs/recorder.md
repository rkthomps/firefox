# The recorder

The recorder writes down what SpiderMonkey's baseline IC pipeline does, so that the Lean model
can be run on the same inputs and compared. It records:

- **generator runs**: every time a baseline IC's stub generator runs, its inputs, what it decided,
  and the CacheIR it wrote. This is step 1 of goal 1b in [feasibility.md](feasibility.md);
- **stub compilations**: every time the baseline CacheIR compiler compiles a CacheIR, the machine
  code, where each op's code begins, and what the replay table did with it (see
  [substitution.md](substitution.md)).

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
- `jit/PhoenixStubCode.h`, `jit/PhoenixStubCode.cpp` (likewise): what the compiler records about
  a stub's code, and substitution ([substitution.md](substitution.md)).
- `jit/BaselineIC.cpp`, `TryAttachStub`: the generator hook. It's the one place where every
  baseline generator that goes through the generic path is built and run, and where the decision
  and whether the stub was attached are both known.
- `jit/BaselineCacheIRCompiler.{h,cpp}`: `compile()` notes the code offset where each op, the
  return and the failure paths begin, into a fixed array, so that it never allocates. The hook
  in `LookupOrCompileStub`, after `compile()`, records the compilation and substitutes code.
- `jit/CacheIRSpewer.{h,cpp}`: the AOT serializer (`SpewCacheIROpsAsAOT`) is compiled under
  `ENABLE_JS_AOT_ICS || JS_PHOENIX`. Two upstream bugs in it, which the recorder hit, are fixed
  by `[BUG FIX]` commits, with reports in [bug-fixes/](../bug-fixes/):
  [0001](../bug-fixes/0001-aot-spewer-typeofeq-operand.md) and
  [0002](../bug-fixes/0002-aot-spewer-immutable-view-kind.md).

## What's recorded

Every line has `v`, the format's version, and `record`, its type. This is version 3 (version 2
had no `build` in process records; version 1 had no compile records, and no `jitOptions` or
`replay`).

### Process records

The first line of each process's records, written when the recorder starts, at the first IC.
Here from the 32-bit ARM shell under QEMU ([backends.md](backends.md)):

```json
{"v":3,"record":"process","pid":1156090,"argv":["obj-js-arm/dist/bin/js","--no-ion",…,"phoenix-tools/tests/bitnot.js"],
 "build":{"backend":"arm","debug":true,"simulator":false,
          "armFlags":{"HasARMv7":true,"HasMOVWT":true,"HasLDSTREXBHD":true,"HasDMBDSBISB":true,"HasVFP":true,
                      "Has32DP":true,"HasVFPv3":true,"HasVFPv4":true,"HasNEON":true,"HasIDIV":true,
                      "HasFPHalfPrecision":true,"HasAlignmentFault":false,"UseHardFpABI":true}},
 "jitOptions":{"icFramePointers":false,"spectreIndexMasking":false,"spectreObjectMitigations":false,
               "spectreStringMitigations":false,"spectreValueMasking":false,"spectreJitToCxxCalls":false},
 "replay":null}
```

- `argv`: the shell's command line, from `/proc/self/cmdline`. It names the test, which a
  record's `loc` doesn't when the IC is in a library (`jit-test/lib/*.js`) or in code made by
  `Function` or `eval`. For jit-tests, the test is the value of the last `-f` or `--module`.
- `build`: the build that wrote the records, which every other record of the process shares.
  - `backend`: the JIT's backend, from `JS_CODEGEN_*`: `x64` or `arm` so far.
  - `debug`: whether it's a debug build, whose stubs have debug-only checks.
  - `simulator`: whether the JIT's code runs in one of SpiderMonkey's simulators
    (`JS_SIMULATOR`), whose stubs aren't the real backend's (see [backends.md](backends.md)).
  - `armFlags`, on ARM only: each of `ARMFlags`' queries, by its name, as the JIT answered them.
    The JIT reads the CPU's features at startup, and its code depends on some of them. They're
    what it uses, not just what the CPU reported: it adds `HasFPHalfPrecision` with VFPv4. A
    few jit-tests choose their own (`--arm-hwcap=vfp`, which is ARMv6 without ARMv7, in
    `basic/bug1870747.js`, `basic/bug1875363.js`, `ion/bug1870756.js`,
    `auto-regress/bug1879939.js` and `wasm/unsupported/requires-armv7.js`;
    `--arm-hwcap=armv7,vfp` in `wasm/arm-hwcap-madness.js`): 6 of 14,146 processes on
    2026-11-09, so filter on `armFlags` before comparing with a model of one CPU.
- `jitOptions`: the `JitOptions` that change the code the compiler emits for some stubs.
- `replay`: the replay table's path (`PHOENIX_REPLAY`), or `null`.

### Generator records

One per generator run, e.g. for `~0`:

```json
{"v":2,"record":"generator","pid":3260920,"kind":"UnaryArith",
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
  - `{"type":"Value","bits":…,"tag":…}`: `bits` is `Value::asRawBits()` as hex, in the build's
    boxing (x64's PUNBOX64, or 32-bit ARM's NUNBOX32, whose upper word is the tag), and
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

### Compile records

One per compilation of a CacheIR by the baseline CacheIR compiler. A zone compiles each kind and
CacheIR once, and every stub with that CacheIR shares the code. For the int32 `~` stub:

```json
{"v":2,"record":"compile","pid":3260920,"kind":"UnaryArith","stubName":"UnaryArith.BitwiseBitNot",
 "cacheir":{"aot":"UnaryArith, 1, 1, 2, JSVAL_TYPE_UNKNOWN, 0, , LASTUSED(1) , OP(GuardToInt32) ID(0) OP(Int32NotResult) ID(0) ",
            "bytes":"0b00006c0100"},
 "code":{"bytes":"834708014989cb…488b7f10ff270f0b",
         "ops":[{"op":"GuardToInt32","offset":4},{"op":"Int32NotResult","offset":24}],
         "tooManyOps":false,"returnOffset":64,"failurePathsOffset":65,
         "relocationBytes":0,"localTracingSlots":0,"makesGCCalls":false},
 "replay":null}
```

- `stubName`: the name of the stub being attached when the code was compiled. Other stubs with
  the same CacheIR may have other names.
- `cacheir`: as in generator records, without the stub fields' values, which belong to each stub,
  not to the code.
- `code`: the C++ compiler's code, even when the replay table replaced it:
  - `bytes`: the instructions, from the code's start (`JitCode::raw()`), in hex;
  - `ops`: each CacheIR op and the offset where its code begins. An op's code runs to the next
    op's offset, and the last one's to `returnOffset`, where the return to the caller begins.
    Before the first op is the entry counter's increment. `failurePathsOffset` is where the
    failure paths begin, to the end of the code. `tooManyOps` is true if the stub had more than
    256 ops, and only the first 256 are listed;
  - `relocationBytes`: the size of the code's relocation tables. Code with relocations embeds
    addresses the linker patches in. The converse doesn't hold: calls into C++ embed absolute
    addresses without relocations;
  - `localTracingSlots`, `makesGCCalls`: what the compiler says about the code, which
    substitution keeps.
- `replay`: `null` without a replay table; otherwise `substituted`, `notInTable` or `refused`.

The code is from a debug build, so it has debug-only checks (e.g. an `int3` after
`Int32NotResult` that checks the upper half of its result is zero).

### Deduplicated records

`phoenix-tools/corpus.py dedupe` writes `unique.jsonl` next to the records: one line per distinct
generator record, i.e. records equal apart from `pid` and `loc`. Each is the record without
those two fields, plus `count` (how many records it stands for), `locs` (their `file:line:column`
locations) and `tests` (the tests they came from, from the process records).

## Not recorded yet

- Generators built outside `TryAttachStub`: `SetProp` and `SetElem` (whose decision can be
  `Deferred`), and `Call` and `SpreadCall`. They need hooks in their own fallbacks.
- Ion's ICs (`IonIC.cpp`). Ion doesn't run baseline stubs, and the runs use `--no-ion`.
- The interpreter operations (goal 2).
