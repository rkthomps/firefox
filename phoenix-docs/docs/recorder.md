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
set. `PHOENIX_RECORD_KINDS` and `PHOENIX_RECORD_TYPES` choose which kinds and which types of
records it writes (see the README's "Recording one script").

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

## What's recorded

Every line has `v`, the format's version, and `record`, its type. This is version 7. Earlier
versions:
- 6 had no `execMemory` in process records, and no `code.address` in compile records;
- 5 had no `abiFunctions` in process records;
- 4 had no `x86CPUInfo` in x86's and x64's process records;
- 3 wrote CacheIR as text, `SpewCacheIROpsAsAOT`'s (the ahead-of-time IC corpus's serializer),
  in `cacheir.aot`, in place of `cacheir.writer`. Recording the writer's state took the
  serializer out of the recorder's path: it gave the three bugs in [bug-fixes/](../bug-fixes/),
  and Lean reads the bytes themselves. Changed on 2026-11-10;
- 2 had no `build` in process records;
- 1 had no compile records, and no `jitOptions` or `replay`.

### Process records

The first line of each process's records, written when the recorder starts, at the first IC.
Here from the 32-bit ARM shell under QEMU ([backends.md](backends.md)):

```json
{"v":7,"record":"process","pid":1156090,"argv":["obj-js-arm/dist/bin/js","--no-ion",…,"phoenix-tools/tests/bitnot.js"],
 "build":{"backend":"arm","debug":true,"simulator":false,
          "armFlags":{"HasARMv7":true,"HasMOVWT":true,"HasLDSTREXBHD":true,"HasDMBDSBISB":true,"HasVFP":true,
                      "Has32DP":true,"HasVFPv3":true,"HasVFPv4":true,"HasNEON":true,"HasIDIV":true,
                      "HasFPHalfPrecision":true,"HasAlignmentFault":false,"UseHardFpABI":true}},
 "jitOptions":{"icFramePointers":false,"spectreIndexMasking":false,"spectreObjectMitigations":false,
               "spectreStringMitigations":false,"spectreValueMasking":false,"spectreJitToCxxCalls":false},
 "abiFunctions":[{"name":"__aeabi_idivmod","sig":null,"address":"0x33d086c"},…,
                 {"name":"JS::ToInt32","sig":"int32_t (*)(double)","address":"0x2409ce9"},…],
 "execMemory":{"base":"0x…","size":146800640},
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
    2026-11-09.
  - `x86CPUInfo`, on x86 and x64 only: each of `CPUInfo`'s queries, by its name. The JIT reads
    the CPU's features with `cpuid` at startup, so they're this machine's unless an option turns
    some off. On 2026-11-10 this machine had all of them, AVX included (this build enables AVX
    for the JIT, `ENABLE_JIT_AVX`). Some jit-tests turn them off: `--no-sse4`, which turns off
    AVX too, in `ion/rinstructions-no-sse4.js`, `typedarray/uint8clamped-round-half-to-even.js`,
    `warp/float32-round-int32-min.js` and `wasm/regress/bug1507314.js`; `--no-ssse3` in
    `regexp/non-ssse3.js`; and `--no-avx`, which turns off AVX2, FMA and F16C too, in
    `regexp/non-avx.js` and three `wasm/gc/` tests, and as an extra run of every test in `wasm/`
    (`test-also` in its `directives.txt`). On 2026-11-10, 459 of 14,147 processes: 449 without
    AVX, 9 without SSE4, 1 without SSSE3.

    So filter on `armFlags` or `x86CPUInfo` before comparing with a model of one CPU.
- `jitOptions`: the `JitOptions` that change the code the compiler emits for some stubs.
- `abiFunctions`: the address of every C++ function that `callWithABI<Sig, fn>` can call, i.e.
  every entry of `ABIFUNCTION_LIST` and `ABIFUNCTION_AND_TYPE_LIST` (`jit/ABIFunctionList-inl.h`),
  in their order. A stub's code embeds the address of each function it calls, as
  `ABIFunction<Sig, fn>::address()` gives it, so comparing a stub's code needs the addresses of
  the process that compiled it. Each entry has:
  - `name`: the function's name as the list spells it;
  - `sig`: the signature, for `ABIFUNCTION_AND_TYPE_LIST`'s entries, which are overloaded (e.g.
    `js::jit::RoundFloat16ToFloat32` three times); `null` for `ABIFUNCTION_LIST`'s, which aren't;
  - `address`: the address, in hex. On 32-bit ARM the shell is Thumb-2 code, so the low bit is
    set, as in the value the JIT embeds.

  125 entries on x64 and 127 on ARM (`__aeabi_idivmod` and `__aeabi_uidivmod` are ARM's only)
  on 2026-11-16, in every process of both full runs. Release builds have 5 fewer, 120 and 122:
  the `js::wasm::Print*` functions are only in debug builds (`WASM_CODEGEN_DEBUG`). On x64, ASLR loads the shell at another
  address in every process (14,147 addresses of `JS::ToInt32` in 14,147 processes); under QEMU's
  user mode, the ARM shell is at the same address in every process (one address in 14,146).

  Calls through computed pointers (`ABIFUNCTIONSIG_LIST`, `DynamicFunction`) have no fixed
  address and aren't in the table. Nor are other addresses code embeds, such as that of the
  message a debug build's `assumeUnreachable` passes to `js::jit::AssumeUnreachable` (a `movabs`
  on x64, in the double path of `TruncateDoubleToUInt32`, for one).
- `execMemory`: the process's executable region, which all JIT code is allocated in
  (`jit/ProcessExecutableMemory.cpp`): `base`, its start in hex, and `size`, its length in bytes
  (`MaxCodeBytesPerProcess`, a constant of the build: 2,143,289,344 on 64-bit, 146,800,640 on
  32-bit). x64's code depends on it: `Assembler::addPendingJump` sorts each jump to a fixed
  address into a near jump, if the target is in the region (`AddressIsInExecutableMemory`), or an
  extended jump, which `finish()` gives an entry of the extended jump table; with no extended
  jumps it ends the code with a `ud2` instead. A call into C++ is always an extended jump, so the
  region decides the code's size. ARM's code doesn't depend on it; it's recorded there too, so
  that both builds' records are alike. `null` if the region isn't reserved yet, which doesn't
  happen in the shell (it's reserved at `JS_Init`). The region is reserved at a random address:
  on 2026-11-18, the x64 release run had a different base in each of its 14,147 processes, and
  the ARM one, under QEMU, 11,497 bases in 14,146.
- `replay`: the replay table's path (`PHOENIX_REPLAY`), or `null`.

### Generator records

One per generator run, e.g. for `~0`:

```json
{"v":4,"record":"generator","pid":3260920,"kind":"UnaryArith",
 "loc":{"file":"phoenix-tools/tests/bitnot.js","line":5,"column":10,"pcOffset":3,"op":"BitNot"},
 "state":{"mode":"Specialized","isFirstStub":true,"numOptimizedStubs":0},
 "args":[{"type":"JSOp","name":"BitNot","byte":20},
         {"type":"Value","bits":"0xfff8800000000000","tag":"int32"},
         {"type":"Value","bits":"0xfff88000ffffffff","tag":"int32"}],
 "decision":"Attach","attached":true,"stubName":"UnaryArith.BitwiseBitNot","writerFailed":false,
 "cacheir":{"bytes":"0b00006c0100",
            "writer":{"numInputOperands":1,"numOperandIds":1,"numInstructions":2,"operandLastUsed":[1],
                      "stubDataSize":0,"typeData":32,"stubFields":[]}}}
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
- `cacheir`: `null` when the writer failed or is empty. Otherwise the `CacheIRWriter`
  (`jit/CacheIRWriter.h`):
  - `bytes`: its buffer, the CacheIR bytecode, in hex. With the kind, it's what the stub code
    cache is keyed on. Ops are numbered by the build: `CacheIROps.yaml` is preprocessed with its
    defines, and two ops exist only under `JS_PUNBOX64`, so the int32 `~` stub is `0b00006c0100`
    on x64 and `0b00006a0100` on 32-bit ARM.
  - `writer`: the writer's state that the bytes don't hold, all of what the compiler reads from
    the writer but the bytes:
    - `numInputOperands`, `numOperandIds` (inputs included) and `numInstructions`;
    - `operandLastUsed`: the entries of `operandLastUsed_`, as many as it has. `writeOperandId`
      resizes it up to the operand it writes, zero-filling, so it covers the operands up to the
      last one an instruction mentions: one before that which no instruction mentions has 0, and
      `operandIsDead` takes it as dead after instruction 0; one after it has no entry, and is
      never dead. So the list can be shorter than `numOperandIds`, e.g. empty for a stub whose
      one instruction mentions no operand
      ([bug 0003](../bug-fixes/0003-aot-spewer-unused-operand.md)). The recorder reads it
      through the public `operandIsDead` and `operandLastUsed`;
    - `stubDataSize`;
    - `typeData`: the `TypeData`'s `JSValueType`, as a number (`JSVAL_TYPE_UNKNOWN` is 32). It
      belongs to the stub, not to the code: `AttachBaselineCacheIRStub` sets it on the stub, and
      the compiler doesn't read it. So stubs with the same CacheIR can have different ones: a
      `ToBool` stub's is 3 (undefined) or 4 (null) for the same bytes;
    - `stubFields`: each stub field's `type`, as its `StubField::Type`'s number (`RawInt32` is 0,
      …, `Double` 16), and its raw `value` in hex, e.g. `{"type":4,"value":"0x0000268d41e20fe0"}`
      for a `WeakShape`.

    Enums are written as numbers, not through name tables, which can miss an enumerator (bug
    0002). `static_assert`s in the recorder stop the build if `StubField::Type` or
    `JSVAL_TYPE_UNKNOWN` is renumbered.

### Compile records

One per compilation of a CacheIR by the baseline CacheIR compiler. A zone compiles each kind and
CacheIR once, and every stub with that CacheIR shares the code. For the int32 `~` stub:

```json
{"v":4,"record":"compile","pid":1420431,"kind":"UnaryArith","stubName":"UnaryArith.BitwiseBitNot",
 "cacheir":{"bytes":"0b00006c0100",
            "writer":{"numInputOperands":1,"numOperandIds":1,"numInstructions":2,"operandLastUsed":[1],
                      "stubDataSize":0,"typeData":32,"stubFields":[]}},
 "code":{"address":"0x…","bytes":"834708014989cb…488b7f10ff270f0b",
         "ops":[{"op":"GuardToInt32","offset":4},{"op":"Int32NotResult","offset":24}],
         "tooManyOps":false,"returnOffset":64,"failurePathsOffset":65,
         "relocationBytes":0,"localTracingSlots":0,"makesGCCalls":false},
 "replay":null}
```

- `stubName`: the name of the stub being attached when the code was compiled. Other stubs with
  the same CacheIR may have other names.
- `cacheir`: as in generator records, without the stub fields' values (but their types), which
  belong to each stub, not to the code. `typeData` belongs to the stub too: here it's that of the
  stub whose attaching compiled the code.
- `code`: the C++ compiler's code, even when the replay table replaced it:
  - `address`: where the code is, in hex: `JitCode::raw()`, the buffer the linker gave
    `Assembler::executableCopy` (`JitCode::copyFrom` passes it `raw()`; the `JitCodeHeader` is
    before it). x64's code depends on it: `executableCopy` links each extended jump straight to
    its target if that's within ±2 GiB of the jump (`X86Encoding::CanRelinkJump`), and through
    its table entry otherwise, writing the target's address into the entry's last 8 bytes (the
    entry's quad is 0 for a jump linked straight). On Linux the region and the shell are mapped
    far apart, so calls into C++ always go through the table: in the x64 release run of
    2026-11-18, all 233,042 table entries of its 996,269 compile records had their target's
    address, and the nearest target was 21.6 TiB from its entry;
  - `bytes`: the instructions, from the code's start (`JitCode::raw()`), in hex;
  - `ops`: each CacheIR op and the offset where its code begins. An op's code runs to the next
    op's offset, and the last one's to `returnOffset`, where the return to the caller begins.
    Before the first op is the entry counter's increment. `failurePathsOffset` is where the
    failure paths begin, to the end of the code. `tooManyOps` is true if the stub had more than
    256 ops, and only the first 256 are listed;
  - `relocationBytes`: the size of the code's relocation tables. Code with relocations embeds
    addresses the linker patches in. The converse doesn't hold: calls into C++ embed absolute
    addresses without relocations (`call(ImmPtr)`'s jumps are `HARDCODED`, which aren't
    written to the tables), at addresses the process record's `abiFunctions` gives;
  - `localTracingSlots`, `makesGCCalls`: what the compiler says about the code, which
    substitution keeps.
- `replay`: `null` without a replay table; otherwise `substituted`, `notInTable` or `refused`.

The code is from a debug build, so it has debug-only checks (e.g. an `int3` after
`Int32NotResult` that checks the upper half of its result is zero).

### Deduplicated records

`phoenix-tools/corpus.py dedupe` derives two directories from the records (`<pid>.jsonl`, which
it doesn't change), and writes them next to them. `record-jit-tests.sh` runs it at the end of a
run. Until 2026-11-10, they were two files, `unique.jsonl` and `unique-compile.jsonl`, and the
generator records' lines had no `op`.

**`unique/`**: one line per distinct generator record, i.e. records equal apart from `pid` and
`loc`'s position (`file`, `line`, `column`, `pcOffset`). Each is the record without those two
fields, plus:
- `op`: its records' `loc.op`, the IC's op. Only seven kinds have it among their arguments
  (`BinaryArith`, `Compare`, `Lambda`, `NewArray`, `NewObject`, `TypeOfEq`, `UnaryArith`);
- `count`: how many records it stands for;
- `locs`: their `file:line:column` locations;
- `tests`: the tests they came from, from the process records.

The lines are split by kind, op, outcome and input types, a file each, so that a tool reads only
the slice it asks for (all of a run's lines are 3.5 GB on x64):

```
unique/<kind>/<op>/<outcome>/<tags>.jsonl
unique/UnaryArith/BitNot/UnaryArith.BitwiseBitNot/int32,int32.jsonl
unique/UnaryArith/BitNot/NoAction/object,int32.jsonl
unique/GetName/GetGName/GetName.GlobalNameValue/none.jsonl
```

- `<outcome>` is the `stubName` of an `Attach` that has one, and the `decision` otherwise. A line
  keeps its `stubName`, which a `NoAction` can have too (`NotAttached`).
- `<tags>` are the `Value` arguments' tags, in order, joined with `,`, as `summary.txt` prints
  them, or `none` if no argument is a `Value`.
- A `/` in a part would be written `%2F`, and a `%` `%25`. Neither occurs now.

Each line is in one file, so a coarser slice is a directory: all of `UnaryArith` is
`unique/UnaryArith/`. A file's lines are sorted by `count`, most first. `unique/index.jsonl` has a
line per file, sorted by `path`: `kind`, `op`, `outcome`, `tags` (a list, `[]` for none), `path`
(from the records' directory), `lines`, `count` (the records its lines stand for) and `bytes`.

**`unique-compile/<kind>.jsonl`**: one line per distinct compile record, a file per kind, for
looking a stub's code up without scanning every record (added on 2026-11-10, for `cachet-lean`'s
`stub-code`). A compile record has no `loc` and no inputs, so it's split by kind only. Records are
the same line when they're equal but for:
- `pid`;
- what belongs to the stub whose attaching triggered the compilation, not to the code: `stubName`
  and the writer's `typeData`. Stubs with other names can have the same CacheIR (`Int32Pos` and
  `Int32ToNumeric`, 10,092 compilations on x64 on 2026-11-10);
- the code's bytes, as long as their length is the same. Code embeds addresses of its process
  (64-bit immediates on x64, where ASLR moves the shell and the heap; `movw`/`movt` pairs on ARM),
  and on ARM `BKPT` immediates count the breakpoints the process emitted before
  (`Assembler::as_bkpt`), so the same stub's bytes differ from process to process. Release builds
  have them too: `assumeUnreachable` is a bare `breakpoint()` there, and
  `CacheRegisterAllocator::ensureDoubleRegister` always emits one after a jump over it (the double
  `~` stub's only `BKPT` in a release build, at offset 56 on 2026-11-17).

and the line keeps its process's configuration, since the same CacheIR can compile to other code
under another one. Each line is a record without `pid` and `stubName`, `typeData` left out of
`cacheir.writer`, and:
- `config`: the process record's `build` and `jitOptions`;
- `code.bytes`: the first record's bytes, in file order, and `code.varying`: the `[start, end)`
  spans of bytes where some record of the line differs from them, `[]` if none does. It's what
  was seen to vary in this run, not all that can: a stub compiled once has `[]` even if it embeds
  an address;
- `code.abiFunctions`: the `abiFunctions` of the first record's process, the addresses
  `code.bytes` embeds for its calls into C++. On x64 they differ in every process, so they
  aren't part of `config`, or lines would never merge. On ARM under QEMU they don't vary, so they
  don't show in `code.varying` either;
- `code.address` and `code.execMemory`: the first record's `code.address`, and its process's
  `execMemory`. With `code.bytes` and `code.abiFunctions`, all four are from that one
  compilation. Like `code.abiFunctions`, they vary between processes, and aren't part of what
  makes records the same line;
- `stubNames` and `typeDatas`: all of the records', sorted;
- `distinctCodes`: how many different `code.bytes` the line stands for, `count` how many records,
  and `tests` the tests they came from.

## Not recorded yet

- Generators built outside `TryAttachStub`: `SetProp` and `SetElem` (whose decision can be
  `Deferred`), and `Call` and `SpreadCall`. They need hooks in their own fallbacks.
- Ion's ICs (`IonIC.cpp`). Ion doesn't run baseline stubs, and the runs use `--no-ion`.
- The interpreter operations (goal 2).
