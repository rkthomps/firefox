# Goal: test the verified CacheIR compiler against SpiderMonkey

The Lean model in `../cachet-lean` translates SpiderMonkey's baseline IC pipeline (stub generator
→ CacheIR → MASM → assembly → machine code) and its interpreter, and proves stubs correct against
it. This effort tests that model against the real SpiderMonkey at the commit the model is built
against, so that a mistranslation shows up as a test failure, not only as a proof that won't go
through.

Where things live:

- `phoenix`: the reference implementation, checked out in `../firefox-phoenix`. It's the Firefox
  commit the model is developed against (`f35f2f1f3ae8`, tagged `phoenix-pin/f35f2f1f3ae8`), plus
  fixes for SpiderMonkey bugs found along the way, each a `[BUG FIX]` commit with a report in
  `phoenix-docs/bug-fixes/`.
- `phoenix-dev`: the branch where the testing infrastructure is developed, checked out in
  `../firefox-phoenix-dev`. It's `phoenix` plus the infrastructure, kept rebased onto `phoenix`.
  The rules for both branches are in [CLAUDE.md](CLAUDE.md).
- `../firefox`: tracks upstream Firefox (`main`).
- `../cachet-lean`: the Lean model and its proofs, documented in its `CLAUDE.md` and `docs/arch/`.

## The four goals

### 1. Run SpiderMonkey's tests against the Lean compiler

Use the bytes the Lean pipeline produces for a stub in place of the bytes SpiderMonkey's CacheIR
compiler produces, then run SpiderMonkey's tests.

- This seems the most tractable. It needs:
  1. modeling the inputs and memory given to the stub generator;
  2. implanting the resulting buffer of bytes into the C++.
- The hard part is modeling the memory given to the stub generator. One strategy: run the C++
  generator, trace its memory reads and writes, and reconstruct from them a partial heap that the
  Lean generator can run against.
- That isn't needed until a stub with memory operations is translated (e.g. `SetProp`). For the
  generators translated so far, this goal should be quite tractable.

### 2. Differentially test the interpreter

Compare the Lean interpreter (the reference that stubs are proved against) with SpiderMonkey's.

- It needs a way to compare values and memory. The memory-tracing infrastructure from goal 1 may be
  reusable for comparing the resulting memory states.
- Only the interpreter paths that the CacheIR compiler can optimize are translated, since those
  are all a stub's correctness proof needs, not the whole interpreter. Everything else is
  `notModeled` (see `CachetLean/VM/` and `docs/arch/vm/interpreter.md` in `../cachet-lean`).

### 3. Differentially test the backend interpreters

The model's instruction-set interpreters (currently x64 and 32-bit ARM) are the trusted base. Test
each against real hardware: this machine is x64.

- It needs a way to compare machine states: registers, flags and memory.
- Only a subset of each instruction set is modeled.
- Stubs from the compiler are one source of test programs. Coverage is likely better with other
  programs built for the purpose too.

### 4. Differentially test the bytes a stub generator emits

Given a stub generator, its inputs and the JS object memory, check that the Lean pipeline produces
the same stub as SpiderMonkey: a code buffer with the same bytes, for each backend architecture.

- The same can be done for the intermediate steps: serialize and compare the CacheIR, then the
  MASM, then the assembly, then the bytes. Matching bytes gives the most confidence.
- It's not hard in principle, but it requires translating SpiderMonkey's encoder, which hasn't been
  done for any stub yet.

## Status

How feasible each goal is, and proposed strategies, are in [FEASIBILITY.md](FEASIBILITY.md).
