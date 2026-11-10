# Substitution: running SpiderMonkey's tests on Lean's stub code

Goal 1 in [feasibility.md](feasibility.md): run SpiderMonkey's tests with the machine code of
baseline IC stubs replaced by the code the Lean pipeline produces. This file is how the
replacement works, the format of the table it reads, and what the Lean driver has to produce.
Built on 2026-11-03, for x64. Only substitute mode exists: compare mode, which would check Lean's
code against C++'s instead of running it, is deferred (see [TODO.md](../TODO.md)).

## How it works

Each zone caches the code of baseline stubs, keyed by the CacheKind and the CacheIR bytes, so
every stub with the same CacheIR shares one piece of code. The baseline interpreter and the
baseline JIT both call it. `LookupOrCompileStub` (`jit/BaselineCacheIRCompiler.cpp`) compiles
the CacheIR on a cache miss, and that's where the hook is, inside `#ifdef JS_PHOENIX`:

1. The C++ compiler runs as usual. Its results that aren't code stay as they are: whether the
   stub makes GC calls (`CacheIRStubInfo`), and the stub's `localTracingSlots`.
2. `phoenix::OnBaselineStubCompiled` (`jit/PhoenixStubCode.cpp`) looks the kind and CacheIR up
   in the replay table named by `PHOENIX_REPLAY`. If it's there, the table's bytes go into a
   fresh assembler (`appendRawCode`), are linked into new `JitCode` (`Linker::newCode`,
   `CodeKind::Baseline`), get the C++ code's `localTracingSlots`, and are cached instead of
   C++'s code. Otherwise C++'s code is used.
3. With `PHOENIX_RECORD` set too, the compile record says what happened: `replay` is
   `substituted`, `notInTable`, or `refused` (see below). See [recorder.md](recorder.md).

Without `PHOENIX_REPLAY`, nothing is substituted. A table that can't be read or parsed crashes
the shell at the first stub compiled, with a message naming the file and line.

## The replay table

A text file. Each line is one entry, three fields separated by spaces or tabs:

```
<CacheKind> <CacheIR bytes in hex> <code bytes in hex>
```

```
# Lines starting with '#', and blank lines, are ignored.
UnaryArith 0b00006c0100 834708014989cb49c1eb2f4181fbf1ff0100…c3488b7f10ff270f0b
```

- `CacheKind` is the kind's name in `CacheIR.h` (`CacheKindNames`), e.g. `UnaryArith`.
- The CacheIR bytes are the key: exactly the bytes of SpiderMonkey's `CacheIRWriter`, i.e. a
  record's `cacheir.bytes`. They're compared byte for byte.
- The code bytes are the whole stub, from its first instruction to its last (see below).
- One entry per kind and CacheIR: a second one is an error.
- Hex digits may be upper or lower case.

## What the Lean driver has to produce

The driver isn't built yet; this is what it has to do. Its input is a recorded run
(`phoenix-tools/record-jit-tests.sh`): generator records, deduplicated in `unique.jsonl`, and
compile records. The record format is in [recorder.md](recorder.md).

**For goal 1b** (Lean's generator and compiler):

1. For each distinct generator record, run Lean's generator on the recorded inputs (for
   `UnaryArith`: `op`, `val`, `res`, as boxed values in the build's boxing). Compare its decision and stub name
   with C++'s. A NoAction from Lean where C++'s stub name belongs to a `tryAttach*` Lean doesn't
   translate is out of coverage, not a mismatch.
2. Where both attach, compare Lean's `CacheIRWriter` with the record's `cacheir`: its buffer
   with `cacheir.bytes`, and its state with `cacheir.writer`. A difference is a mismatch, and gets
   no table entry.
3. Where they're equal, compile Lean's CacheIR to x64 bytes, and write
   `<kind> <record's cacheir.bytes> <Lean's bytes>`. The key is C++'s CacheIR bytes, which is
   right because step 2 showed Lean's CacheIR is the same.

**For goal 1a** (Lean's compiler only): the same from the compile records, without steps 1 and
2: every `cacheir` SpiderMonkey compiled, compiled by Lean.

**What Lean's code must be:**

- A complete baseline stub, called the way C++'s is: count the entry in the stub's
  `enteredCount_`, run the ops, return through `EmitReturnFromIC`, and on a guard's failure,
  restore the inputs and jump to the next stub through `next_` (`EmitStubGuardFailure`). Stub
  fields are read at `StubDataOffset` from `ICStubReg`. This is what `cachet-lean`'s
  `compileBaselineCacheIRStub` models.
- Independent of where it's placed: no absolute addresses, no relocations. It's copied to an
  address the table can't know. Ops that call into C++ (`callVM`, `callWithABI`) or embed
  runtime addresses need the process's addresses, which the table can't carry yet.
- For the configuration the run used: the process record's `jitOptions`. Replay refuses to start
  with IC frame pointers on. The Spectre mitigations change the code of some ops.
- Not the same bytes as C++'s: different encodings and lengths are fine, as long as it behaves
  the same.

**Entries that can't be used:** if C++'s own code for a stub has relocations, the entry is
`refused`, and C++'s code is used: bytes alone can't carry what the linker patches in.

## Running it

```
phoenix-tools/record-jit-tests.sh --replay TABLE LABEL [KINDS]
```

The run records as usual, with the table copied into it, and ends with `corpus.py replay`'s
summary: how many stub compilations were `substituted`, `notInTable` and `refused`, and which
stubs were substituted, in how many tests. Compare its failures with those of a run without the
table: with all of jit-tests, those are `baseline/blinterp-trial-inlining.js` and
`cacheir/bound-function-trial-inlining.js`, which `--no-ion` makes fail.

For one script, set `PHOENIX_REPLAY=TABLE` (and `PHOENIX_RECORD` to see the compile records):

```
PHOENIX_REPLAY=TABLE obj-js-x64/dist/bin/js --no-ion --baseline-eager phoenix-tools/tests/bitnot.js
```

## Testing the mechanism

`phoenix-tools/replay-table.py RECORDS_DIR [--kind K] [--stub NAME] [--cacheir HEX]` writes a
table of SpiderMonkey's own code from a run's compile records. Replaying it must change nothing.
C++'s code can embed absolute addresses of the process that compiled it, which relocation tables
don't all record, so the tool skips stubs whose code differs between processes, and a table
should be restricted to stubs known not to embed any.

Checked on 2026-11-03, with `phoenix-tools/tests/bitnot.js` and the int32 `~` stub
(`UnaryArith 0b00006c0100`):

- its own code, replayed: the test passes, and the stub is `substituted`;
- its code with `not ecx` changed to `neg ecx`: the test fails (`got -1, expected -2`), so the
  table's code is what runs;
- its code after a `nop`, so every instruction moves: the test passes;
- malformed tables (an unknown kind, odd hex, a duplicate entry, two or four fields, a missing
  file) crash with a message naming the file and line;
- all of jit-tests with its own code replayed
  (`../phoenix-runs/2026-11-03-bitnot-identity-replay`): the same results as without a table
  (14,183 passed, and the two failures above), with the stub substituted in 86 compilations, in
  41 tests.
