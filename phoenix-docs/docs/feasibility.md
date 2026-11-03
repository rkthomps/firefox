# Feasibility of the testing goals, and how to reach them

Written 2026-11-02, against `cachet-lean` at `35420cb` ("Working on backend-independent theorem")
and Firefox at `f35f2f1f3ae8` (the pin, tagged `phoenix-pin/f35f2f1f3ae8`). The goals are in
[GOAL.md](../GOAL.md).

## Summary

All four goals are feasible. SpiderMonkey already has much of the plumbing, and one observation
makes the most valuable goal easier than [GOAL.md](../GOAL.md) assumes: **baseline stub code depends
only on the stub's CacheIR, not on the heap.** So the Lean *compiler* can be tested, and its stubs
substituted into real test runs, without modeling any memory. Heap modeling is needed only to test
the Lean *generators* and the *interpreter* on ops that touch memory, and none are translated yet.

| Goal | Feasible? | Blocked on | Signal today |
|---|---|---|---|
| 1a. SM tests on Lean-compiled stubs (C++'s CacheIR) | Yes, now | A recorder and a substitution hook | Only `~int32` stubs, but the whole pipeline runs |
| 1b. The same with the Lean generator too | Yes for `UnaryArith`. Heap generators later | Heap modeling, for heap generators | Same stub |
| 2. Interpreter vs C++ | Yes. Trivial now, harder with heap | Heap comparison, when ops touch memory | `BitNotOperation` on int32 |
| 3. x64 interpreter vs hardware | Yes, now, on this machine | A small C harness | High: the trusted base, 11 instructions |
| 3. ARM interpreter vs hardware | Yes, with an ARM oracle | ARMv7 hardware, or qemu as a stopgap | High, once an oracle exists |
| 4. Stub bytes, x64 | Instructions now. Bytes after translating the encoder | `X86Encoding::BaseAssembler` | Instruction-level match |
| 4. Stub bytes, ARM | Probably bytes now | An ARM build of SpiderMonkey (the simulator) | Word-level match |

Recommended order: the recorder and corpus, then x64 instruction-level comparison, then
substitution, with the x64 hardware harness built in parallel. Details are in "Plan" at the end.

## Where the model stands

From `../cachet-lean/docs/arch/layers.md` and `plan.md`:

- **x64** ([x64/overview.md](../../../cachet-lean/docs/arch/x64/overview.md)): a decoder and an
  interpreter following the SDM. These are the trusted base. 11 instructions: MOV, SHR, NOT, ADD,
  OR, XOR, CMP, TEST, Jcc, RET, JMP. There's an untrusted table-driven encoder whose round trip is
  proved, and an assembler with SpiderMonkey's `Assembler` methods as helpers.
- **32-bit ARM** ([arm/overview.md](../../../cachet-lean/docs/arch/arm/overview.md)): ARMv7-A, ARM
  state only. Nine instructions, a decoder over the encoding diagrams as data, an encoder with the
  round trip proved, and an assembler built from translated `as_*` methods (including
  `Imm8::EncodeImm`).
- **MASM**: one shared interface with x64 and ARM instances, and their laws proved. Seven ops:
  `not32`, `mov`, `moveValue`, `unboxNonDouble`, `tagValue`, `branchTestInt32` and
  `add32(Imm32, Address)`.
- **CacheIR**: 2 of the 468 ops in `CacheIROps.yaml` (`GuardToInt32`, `Int32NotResult`). Also the
  register allocator, failure paths, and the whole baseline driver
  (`compileBaselineCacheIRStub`).
- **Generators**: `UnaryArithIRGenerator`, trying only `tryAttachBitwise`, and writing a stub only
  for an int32.
- **Reference interpreter**: `BitNotOperation` on an int32. Everything else is `notModeled`.
- **Proofs**: the top theorem for `~int32` on x64 is stated and assembled from lemmas between the
  IRs, which are mostly stated rather than proved. A backend-independent version is in progress.

What matters for testing:

- **The model is executable.** `CachetLean/` has no `noncomputable` definitions, and the tests
  already run compiled code (`#guard`) for the encoding sweeps. A compiled Lean driver can run the
  model over large corpora.
- **Errors already classify outcomes.** `Error.notModeled` (a gap in the model),
  `Error.assertFailed` (a `MOZ_ASSERT` that SpiderMonkey would hit too) and
  `Error.auxAssertFailed` (the model asking more than C++ checks) map directly onto test verdicts:
  out of coverage, both should fail, and investigate.
- **Every level is computed from the one before** in `Examples/Int32NotStub.lean`: inputs →
  CacheIR → MASM → assembly → bytes → runs. The differential driver is that file generalized over
  its inputs.

## What SpiderMonkey already provides

Read at the pinned commit:

- **The stub cache is keyed by CacheIR alone.** `LookupOrCompileStub` in
  `jit/BaselineCacheIRCompiler.cpp` looks code up by `CacheIRStubKey::Lookup(kind,
  ICStubEngine::Baseline, writer.codeStart(), writer.codeLength())`. Only on a miss does it run
  `BaselineCacheIRCompiler`. Baseline stubs read their fields from the stub object through
  `ICStubReg` at run time, so the same code serves every stub with that CacheIR in the zone.
  - The exceptions are absolute addresses the `Linker` patches in: VM-call trampolines and runtime
    addresses. None of these appear in the ops translated so far. When they do, `JitCode`'s
    relocation tables say where they are.
- **A portable serialization of CacheIR, and a way back.** The ahead-of-time IC corpus
  (`--enable-aot-ics`):
  - `SpewCacheIROpsAsAOT` in `jit/CacheIRSpewer.cpp` writes a writer as one text line: the kind,
    `numInputOperands`, `numOperandIds`, `numInstructions`, `typeData`, `stubDataSize`, the stub
    fields' types, `operandLastUsed`, and the ops with their arguments.
  - `CacheIRAOTStub` (`jit/CacheIRAOT.h`) and `CacheIRWriter(cx, const CacheIRAOTStub&)` rebuild a
    writer from it. `FillAOTICs` compiles the whole corpus (`js/src/ics/IC-*`, turned into a header
    by `generate_aot_ics_header` in `jit/GenerateCacheIRFiles.py`).
  - With `--enforce-aot-ics`, `LookupOrCompileStub` dumps the first IC that isn't in the corpus
    and aborts. With `AOT_ICS_KEEP_GOING=1` it dumps every one and keeps going, which collects a
    corpus from whole test suites.
  - These fields are exactly the Lean `CacheIRWriter`'s (`ops`, `numInputOperands`,
    `nextOperandId`, `nextInstructionId`, `stubFields`, `operandLastUsed`;
    [cacheir/cacheir.md](../../../cachet-lean/docs/arch/cacheir/cacheir.md)). That makes this format
    the natural exchange format between the two.
  - **But `--enable-aot-ics` builds only with the portable baseline interpreter (PBL).** In a build
    with the native JIT, `BaselineCacheIRCompiler.cpp` stops with `#error AOT ICs are only
    supported (for now) in PBL builds.` That's because loading the corpus when a `JitZone` is
    created needs JIT state that doesn't exist yet. So the loader (`FillAOTICs`) isn't available
    to us.
  - The serializer is a self-contained block of `CacheIRSpewer.cpp`, plus one declaration in
    `CacheIRSpewer.h`. So `phoenix-dev` can compile it alone by widening those two
    `#ifdef ENABLE_JS_AOT_ICS` guards to a flag of its own. Failing that, record the raw CacheIR
    bytes and have the Lean side parse the binary format.
- **An assembly trace.** In a `JS_JITSPEW` build (debug builds have it), `IONFLAGS=codegen` prints
  every x86 instruction with its offset as it's emitted (`AssemblerBuffer-x86-shared.h`), and each
  CacheIR op's `emit*` method logs its name on the same channel.
- **Generator inputs.** Every generator's `trackAttached` logs its inputs through `CacheIRSpewer`
  (`CACHEIR_LOGS=<file>`). For `UnaryArith` that's `val` and `res`. But doubles are printed to three
  decimals and objects are summarized, so it shows where to hook, not what to record.
- **Raw code in, code out.** `MacroAssemblerX86Shared::appendRawCode` appends bytes to an assembler
  buffer, and `Linker::newCode` turns the buffer into `JitCode`. ARM's buffer
  (`IonAssemblerBufferWithConstantPools`) has `appendRawCode`, but ARM's `Assembler` doesn't
  forward it yet. That's a one-line wrapper.

## Infrastructure shared by the goals

Build these once. Every goal uses them.

1. **A recorder in SpiderMonkey** on `phoenix-dev`, compiled in only with `--enable-phoenix`, and
   off unless `PHOENIX_RECORD=<dir>` is set. It writes JSON Lines. How to build, run and read it
   is in [recorder.md](recorder.md).
   - in `LookupOrCompileStub`, after `comp.compile()`: the kind, the CacheIR in AOT text and as raw
     bytes, the code (`code->raw()`, `instructionsSize()`), its relocations, and the code offset at
     which each CacheIR op begins. The offsets come from a one-line hook in the compile loop. They
     let a mismatch be blamed on one op without serializing MASM. *Not built yet.*
   - **at every generator run, in `TryAttachStub` (built):** the generator's inputs (the
     arguments it's constructed with, `Value`s as raw bits), the IC state, its decision and stub
     name, whether the stub was attached, and the CacheIR it wrote, in AOT text and as bytes.
     `TryAttachStub` is better than `trackAttached`: one hook covers every generator, and the
     decision is known there. Generators built outside it (`SetProp`, `SetElem`, `Call`,
     `SpreadCall`) still need their own hooks;
   - at each translated interpreter operation, e.g. `js::BitNot` from `DoUnaryArithFallback`: the
     operands and result as raw bits, and whether it succeeded. *Not built yet.* For `BitNot`,
     the generator records already have it: `res` is `~val`.

   Record by content, not call order. Which stubs attach depends on GC timing, trial inlining and
   stub folding (`TrialInlining.cpp` and `StubFolding.cpp` also call
   `AttachBaselineCacheIRStubLocked`, and the hook in `LookupOrCompileStub` sees those too).
   Deduplicate by hash, as `js/src/ics/remove-duplicates.py` does.
2. **A Lean driver**: a compiled executable that reads records, runs the model, and writes one
   verdict per record: `match`, `mismatch` (with the first difference), `notModeled` (with what),
   or `assert`. I'd put it in its own Lake package that `require`s `cachet-lean` by path, so
   `cachet-lean`'s library and its rules stay as they are, and the driver can use `IO` and
   `Lean.Json` freely. It needs:
   - a parser from AOT text to `CacheOp` for the translated ops, reporting any other op as
     `notModeled`. Generating it from `CacheIROps.yaml`, as SpiderMonkey generates its own readers,
     scales to every op, and doubles as the CacheIR encoder `plan.md` already plans;
   - `Value`s from raw bits, at each backend's boxing.
3. **A corpus**, collected by running jit-tests and jstests with the recorder on, and stored outside
   the tree. Every verdict run reports **coverage**: the share of recorded stubs, generator calls
   and interpreter operations the model handled. That number is a progress metric for the whole
   project, not just for testing.
4. **Builds.** The browser objdir has no JS shell, so add JS-shell objdirs selected with
   `MOZCONFIG`. `obj-js-x64` is built, with the recorder, and so is `obj-js-pbl` (upstream's AOT
   IC build, for reproducing bugs); `obj-js-armsim` isn't yet. Their mozconfigs are in
   [spidermonkey.md](spidermonkey.md).

## Goal 1: SpiderMonkey's tests on the Lean pipeline's stubs

**Feasible now, in two variants.**

- **1a. Lean's compiler, C++'s generator.** The input is the CacheIR the C++ generator wrote, so
  nothing needs modeling: not the inputs and not the heap. It tests the compiler, the MASM layer and
  the encoder in real executions.
- **1b. Lean's generator too.** The input is the generator's inputs, so it tests everything,
  generator included. It needs the inputs, and for heap generators the heap (see "Modeling the
  heap" below). For `UnaryArith` the inputs are `op`, `val` and `res`, three words.
  - *First corpus* (2026-11-03, all jit-tests with `--no-ion --baseline-eager`, in
    `../phoenix-runs/2026-11-03-unaryarith-jittest`): `tryAttachBitwise` attached for an int32
    253 times, in 41 tests, from 35 distinct inputs, all with the same CacheIR (`GuardToInt32`,
    `Int32NotResult`). It also attached 109 times for doubles, strings, booleans, null and
    undefined, which Lean reports as `notModeled`. Two identical runs recorded 139,327 and
    139,231 `UnaryArith` generator runs, so record by content, not by count.

**Substitution.** Put the hook in `LookupOrCompileStub`, after `comp.compile()` and before
`putBaselineCacheIRStubCode`, so every stub sharing that CacheIR gets the same code:

- *compare mode*: keep C++'s code, and log whether Lean's code matches it;
- *substitute mode*: if Lean has code for this kind and CacheIR, assemble it into a fresh assembler
  with `appendRawCode`, link it, and use it. Otherwise keep C++'s code and count a `notModeled`.

Running the C++ compiler first keeps `makesGCCalls()` and `CacheIRStubInfo` exactly as they were,
and gives compare mode for free.

**Getting Lean's code into the process**, simplest first:

1. **Record, then replay (recommended to start).** Run the tests once with the recorder. Have the
   driver compile every recorded CacheIR into a table keyed by its hash. Run again with
   `PHOENIX_REPLAY=<table>`. There's no Lean runtime in Firefox and no IPC, and replay is
   deterministic. A stub missing from the table falls back to C++ and is counted.
2. **A Lean server over a pipe**, queried on a cache miss. It handles stubs the first run didn't
   see. A stub is compiled once per zone, so the latency doesn't matter.
3. **Linking Lean's generated C into SpiderMonkey.** That means the Lean runtime and its
   initialization inside the engine. Avoid it unless the other two prove too slow.

**Making stubs actually run:**

- Run the shell with `--no-ion`. Ion doesn't run baseline stub code: Warp transpiles the CacheIR to
  MIR, and Ion ICs compile their own. With Ion on, hot code leaves the stubs being tested.
- Use `--baseline-eager` (or `--fast-warmup`) so that ICs attach early. The baseline interpreter
  uses the same stubs.

**What's needed of Lean's code:**

- It must not depend on where it's placed. With no pinned labels, `assembleBytes_base` (stated in
  `X64/AsmLemmas.lean`) claims exactly that, and a run with substitution tests the claim.
- Once ops need trampolines or runtime addresses, Lean must report its absolute addresses so the
  `Linker` can patch them, or substitution must skip those stubs.
- Lean's x64 encoding is longer than SpiderMonkey's (always a SIB byte, its own choice between
  forms). The code size differs, which is harmless.

**The oracle** is the tests themselves, plus SpiderMonkey's debug assertions. A cheap extra is the
classic SpiderMonkey differential check: run each test with `--no-baseline --no-blinterp` (C++
only) and with substituted stubs, and compare the output.

**Today** this exercises only `~` on int32s. Its value now is the plumbing, and making coverage a
number that grows with each translated op.

## Goal 2: the interpreter against SpiderMonkey's

**Feasible now for what's translated.** The C++ side is just recording.

- **Inputs:** record operands and results where the C++ operation runs. For `BitNot`,
  `DoUnaryArithFallback` calls `js::BitNot`, which is `BitNotOperation`. Also run the tests with
  `--no-baseline --no-blinterp`, so that every execution goes through the C++ interpreter, not only
  the ones that miss in the IC. Add synthetic values for the edges: `INT32_MIN`, −1, 0, −0, NaN,
  doubles just outside the int32 range, booleans, null, undefined, strings, BigInts, objects.
- **Checks:**
  1. Where Lean's operation is modeled, it gives the same result, or fails as C++ does.
  2. The check the theorem cares about: **for every input on which a stub succeeded, Lean's
     operation is modeled.** A `notModeled` there means the reference is under-translated for a
     path a stub covers, and the proof will get stuck on it. Whether the stub succeeded can come
     from the recorder (did the fallback see the input) or from Lean's own `evalStub`.
- **Memory**, once an operation touches the heap: compare the writes. See "Modeling the heap"
  below. With a pre-state snapshot, C++'s write set comes cheaply from Linux's soft-dirty page bits
  (`/proc/<pid>/clear_refs` and `pagemap`): clear them before the operation, read them after, and
  diff only the pages written. Suppress GC across the operation so the collector's writes don't
  count.

The interpreter is only translated along paths that stubs reach. So a high `notModeled` rate on
general inputs is expected, and isn't a failure. Only check 2 turns `notModeled` into a failure.

## Goal 3: the backend interpreters against hardware

**x64: feasible now.** A small C harness on this machine.

- **Placing the test:** map the model's code and data regions at the model's own addresses
  (`mmap` with `MAP_FIXED_NOREPLACE`), code executable and data not. The model keeps code and data
  in separate maps over one address space, so tests must keep them disjoint. Load the general
  registers and RFLAGS from a struct, then jump to the entry.
- **Halting:** the model halts when `rip` reaches an address with no code. Natively, leave the
  exit addresses (the return address, the fallback stub's code) unmapped or non-executable. The
  jump then faults with the exit address in `rip`, which is the model's halt.
- **Capture:** a `SIGSEGV`/`SIGBUS`/`SIGILL` handler reads the `ucontext` (all registers, RFLAGS,
  `rip`, the faulting address) and dumps the data regions. Fork once per test so that a crash stays
  contained.
- **Compare:**
  - registers and memory exactly;
  - flags only where the model says `some`, since `none` means undefined;
  - the model's `Stuck` reasons against the signal: unmapped memory is `SIGSEGV` with `si_addr`;
    a non-canonical address is #GP, which arrives as `SIGSEGV` with `si_addr` 0; #SS arrives as
    `SIGBUS`. Check each mapping experimentally once.

  Flags the model leaves undefined on entry are random in the native run, so the model's verdict
  can't depend on them. A model run that's stuck on reading one is a valid outcome, not a
  mismatch.
- **Programs**, roughly in order of value:
  1. per instruction, every row of the opcode table, with edge-case operands, random registers and
     random input flags. The Lean encoder makes the bytes. It isn't trusted, but both sides run the
     same bytes, and the trusted decoder reads them;
  2. random short sequences of modeled instructions, branches included;
  3. stubs from the Lean compiler;
  4. stubs from the SpiderMonkey corpus, decoded by Lean. A stub that won't decode marks an
     instruction to model next.
- **The decoder is trusted too**, so test it on its own: random bytes, and the corpus's bytes,
  decoded by Lean and by Intel's XED (the reference), comparing lengths and operands.
  `cachet-lean` already compares against GNU `objdump` and `llvm-mc`.
- **The model's assumptions about the processor hold for ordinary Linux processes on this
  machine:** CPL 3; no LAM, LASS or alignment checking; and CET off, since Linux user shadow stacks
  are opt-in.

**32-bit ARM: feasible, with an oracle.** This machine can't run ARM code, and `qemu-arm` isn't
installed. The same harness ports over: an `arm_*` `ucontext` and CPSR in place of RFLAGS. Oracles,
from cheapest:

1. **qemu-arm user mode.** It's an emulator, not hardware, but a good first filter for integer
   instructions.
2. **SpiderMonkey's ARM simulator** (`jit/arm/Simulator-arm.cpp`, in the `armsim` build). It isn't
   hardware either. But it's where SpiderMonkey's own ARM stubs run on this machine, so a
   disagreement finds a bug either in the model or in the simulator.
3. **Hardware that runs AArch32 user code**, e.g. a Raspberry Pi with a 32-bit userland. Many
   recent Arm cores have dropped AArch32, so check before relying on a cloud machine. ARMv8's
   AArch32 can differ from ARMv7 where ARMv7 says UNPREDICTABLE. The model already treats those
   cases as `unpredictable`, so the harness compares only defined behaviour.

The hardware also settles a question [arm/overview.md](../../../cachet-lean/docs/arch/arm/overview.md)
leaves open: which SCTLR.A Linux runs user code with. An unaligned `LDR` either faults or doesn't.

## Goal 4: the bytes a stub generator emits

**x64: equal instructions now, equal bytes after translating the encoder.**

- Byte equality isn't expected today, by design. The encoder writes the decoder's table rows in a
  fixed form: always a SIB byte, `89` where SpiderMonkey writes `8B` for `movl`, and no `rel8` for
  backward jumps ([x64/encoder.md](../../../cachet-lean/docs/arch/x64/encoder.md),
  [x64/assembler.md](../../../cachet-lean/docs/arch/x64/assembler.md)). The assembler was written so
  that its *instructions* are the ones SpiderMonkey's bytes decode to.
- So compare **instructions**. Decode SpiderMonkey's recorded bytes with the Lean decoder, and
  compare them with Lean's `asmOf` for the same CacheIR. Compare jumps by the instruction they
  land on, not by offset, since lengths differ. This gives the "serialize the assembly" level with
  no instrumentation in SpiderMonkey. It also runs the trusted decoder over real SpiderMonkey code.
- **Byte equality** then needs `X86Encoding::BaseAssembler` translated: its formatter
  (`oneByteOp`, `memoryModRM`, …) and how it patches forward jumps when a label is bound. This is
  deferred in [x64/encoder.md](../../../cachet-lean/docs/arch/x64/encoder.md). It's mechanical, and
  the existing round-trip proof shows what proving it would take.

**ARM: probably equal bytes already.**

- The assembler is translated from `as_*`, including `Imm8::EncodeImm`, and an ARM instruction has
  essentially one encoding. A word-for-word comparison is the first thing to run.
- SpiderMonkey's ARM bytes come from the `armsim` build, which emits real ARM code on this machine.
  The alternative is a cross-compiled shell under qemu.
- The comparison will also settle one choice [examples.md](../../../cachet-lean/docs/arch/examples.md)
  records as the model's own: which of `takeAnyValue`'s two `takeAnyGeneral()` calls runs first.
  C++ leaves argument evaluation order unspecified, and clang picks one.

**The intermediate levels:**

- **CacheIR:** compare the AOT text, or the raw bytes, of what each generator wrote. For goal 1b,
  this is the first check to run.
- **MASM:** SpiderMonkey has no serialization of MASM, and adding one means touching every op.
  Recording the code offset where each CacheIR op begins (see the recorder above) blames an
  instruction-level mismatch on one op, which is what a MASM trace would have been for. Instrument
  MASM only if that proves too coarse.
- **Assembly:** the decoded instructions above. `IONFLAGS=codegen` gives SpiderMonkey's own view
  of the same thing.
- **Bytes:** after relocations are masked (see "What SpiderMonkey already provides").

## Modeling the heap

This is needed for goals 1b and 2 once a translated generator or operation reads or writes the
heap. Nothing translated does yet.

**Decide first how the Lean model represents the heap.** Either:

- *bytes at addresses, with C++'s layouts*, as `BaselineIC.lean` already does for stub objects.
  Then any snapshot of process memory feeds the model directly;
- *abstract objects* (shapes, slots, prototypes as Lean structures). Then something must lift
  memory into objects, using the build's layouts.

Either way, the debug build's DWARF records the exact offsets of every C++ field. Checking the
model's layout constants against it (`ICStub::enteredCount_` at 8, …) is a small, cheap test worth
adding now.

**Ways to give the Lean generator the heap it ran on:**

1. **Trace the C++ generator's reads** (the plan in [GOAL.md](../GOAL.md)), with DynamoRIO or Pin,
   between marker calls around `tryAttachStub`. This gives a partial heap. A faithful translation
   reads only what C++ read, so if Lean reads outside the traced set, the translation reads
   something C++ didn't. That's a mismatch report in itself. Filter out reads of the writer's own
   buffers, `JitOptions`, and the spewer.
2. **Snapshot with `fork()`, and read lazily.** At the generator's entry, fork. The child is a
   copy-on-write snapshot of the whole heap, the GC chunks and malloc'd slots included. It stops
   and serves byte reads over a pipe (`memcpy` and `write`, nothing that allocates or locks). The
   Lean driver asks for addresses as it needs them, so nothing has to be predicted. The parent
   carries on. The same snapshot is the pre-state for goal 2's memory comparison.

Option 2 is less work and can't miss a read. Option 1 gives the read-set check. They combine
well: snapshot for the values, trace for the check, and add the trace when the first memory-reading
stub arrives.

## Plan

Each step is useful on its own. The x64 hardware harness (M4) doesn't depend on the others and can
start at any time.

- **M0. Builds:** the `js-x64` objdir, and run jit-tests on it once to get a baseline. *Done.*
- **M1. Recorder and corpus:** the stub, generator and interpreter records, plus per-op code
  offsets. Then a corpus from jit-tests. *Generator records done, with a `UnaryArith` corpus
  from jit-tests (see [recorder.md](recorder.md)).*
- **M2. Lean driver, compiler side:** parse AOT text into `CacheOp`, compile, and compare
  instructions on x64 (goal 4). This is the first real signal, with a coverage report.
- **M3. Substitution (goal 1a):** compare mode, then replay. Then jit-tests with `--no-ion
  --baseline-eager`, and coverage tracked from run to run.
- **M4. x64 hardware harness (goal 3):** per-instruction sweeps, then corpus stubs, then a decoder
  check against XED.
- **M5. Generator and interpreter checks (goals 1b and 2):** for `UnaryArith` and `BitNot`.
  Neither needs a heap.
- **M6. ARM:** the `armsim` build, an ARM corpus, word-level comparison, and an ARM oracle (qemu
  first, hardware later).
- **M7. Heap:** decide the representation, then snapshots, and tracing if wanted, before the first
  stub that reads memory.
- **M8. x64 byte equality:** translate `BaseAssembler`'s encoding.

## Risks and open questions

- **Coverage is small:** 2 of 468 CacheIR ops. For now, nearly every stub in a test run is C++'s.
  The infrastructure pays off as ops are translated, and the coverage number makes that visible.
  Prioritizing ops by how often the corpus uses them is one way to choose what to translate next.
- **Two corpus bugs, fixed on `phoenix-dev`** by `[BUG FIX]` commits, with reproductions in
  [bug-fixes/](../bug-fixes/). In the AOT serializer, `spewTypeofEqOperandImm` passes the argument's
  name as the format string, so `LoadTypeOfEqObjectResult` is written wrong (latent upstream:
  PBL's hybrid ICs never attach that stub). And `ArrayBufferViewKindName` doesn't handle
  `ArrayBufferViewKind::Immutable`, so serializing an op with that kind hits `MOZ_CRASH`. Both
  are still on upstream `main`, which makes them good first bugs to file.
- **The AOT serializer's guard is widened on `phoenix-dev`** to `ENABLE_JS_AOT_ICS ||
  JS_PHOENIX`. The block compiles without the rest of the AOT code.
- **Lean evaluation speed:** the model's memory is a function, which is fine for stubs but may be
  slow for large sweeps. If so, speed up the driver's own data structures, not the model's.
- **Questions for you:**
  1. Should the Lean driver be its own package (my recommendation) or part of `cachet-lean`?
  2. Which heap representation do you want: bytes with C++'s layouts, or abstract objects? It
     decides how M7 works.
  3. Do you have AArch32-capable ARM hardware, or is qemu acceptable for now?
  4. Should `phoenix-dev`'s hooks stay minimal, so it rebases cheaply onto a newer pin, or is a
     larger fork acceptable?
