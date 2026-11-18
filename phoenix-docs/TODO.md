# TODO

Work deferred for later. Remove an item when it's done.

## Branches: follow-ups

The branch structure is in place (see [docs/branches.md](docs/branches.md)). Left to do:

- Scripts for the two workflows, moving a fix to `phoenix` and moving the pin, if doing them by
  hand gets tedious.
- A way for icarus to clone `phoenix` from the fork (`rkthomps/firefox`) instead of the upstream
  pin. Needed now: as of 2026-11-18, fixes 0004 and 0005 change code the model translates.
  `PHOENIX_MOZ_REV` already chooses the revision; the clone URL is hard-coded.
- A list of retired fixes: when the pin moves, a fix upstream has made is dropped, and its report
  goes with it (still reachable from the `phoenix-final/<old-pin>` tag).
- The pin tag, `phoenix-pin/f35f2f1f3ae8`, is only local. Decide whether to push it to `origin`,
  as the branches are (see "Pushing" in [docs/branches.md](docs/branches.md)).

## The recorder: `baseline/bug1238815.js` times out when every kind is recorded

Found on 2026-11-09. The test evals one script of 532,480 additions (`Array(65 << 13).join(" +
i")`), each with its own ICs: a `GetName` for `i` and a `BinaryArith` for `+`. On x64 it passes in 2.9 s recording only `UnaryArith`,
and under QEMU in 24 s not recording. Recording every kind, it times out on both: on x64 after
300 s with 452,458 records, under QEMU after 1,200 s with 257,199. So it's the recorder's cost,
not QEMU's, and it's the one test of the ARM run that fails for want of time (see "jit-tests
under QEMU" in [docs/backends.md](docs/backends.md)).

Likely cause, not checked: each generator record's `loc` calls `PCToLineNumber`, which walks the
script's source notes from its start to the pc, so a record costs time linear in its IC's
offset, and a script with n ICs costs O(n²). Check it by timing records against `pcOffset`, or
with a profiler. If it's that, compute `loc` incrementally or cache it per script, inside
`JS_PHOENIX`. Until then the test times out in runs that record every kind, which costs little:
of the 257,199 records it wrote under QEMU, all but 45 are two stubs, `GetName.EnvironmentName`
and `BinaryArith.Int32Add`, over and over.

## The USR index: names of an instantiation's members

A flaw of `usr-index.py` ([docs/usr-index.md](docs/usr-index.md)), found on 2026-11-06. A member
specialized for one instantiation of a class template,
`template <> bool TypedArrayObjectTemplate<int64_t>::convertValue(…)`, is named
`(anonymous namespace)::TypedArrayObjectTemplate<long>::convertValue` on x64 and `…<long long>…`
on ARM: its class is named from the instantiation's display name, whose arguments are canonical,
since an implicit instantiation has none written. 6 records in each build. Fix: name the scope as
the declaration writes its qualifier, which the pretty-printed declaration has, as for a
function's arguments. It changes names, so the format's doc and `cachet-lean`'s
`docs/arch/provenance.md` change with it.

## Distinct counts in the corpus summary

`corpus.py dedupe`'s summary gives, per kind, stub name and input types, only how many records
there were. Add how many of them are distinct (the lines of `unique/`, which `unique/index.jsonl`
counts by kind, op, outcome and input types), and perhaps how many tests and source locations
they came from. A row's record count follows how many IC sites the tests have, so on its own it
overstates how much there is to check: `UnaryArith.BitwiseBitNot int32,int32` is 253 records but
only 35 distinct ones.

## A run's process records in their own directory

A run's `records/` holds about 14,000 `<pid>.jsonl` files, one per shell process, next to the
derived `unique/` and `unique-compile/`, which a listing then buries. `record-jit-tests.sh` could
set `PHOENIX_RECORD` to a subdirectory, e.g. `records/processes/`, so that `records/` holds only
it and the two trees. `corpus.py` (`dedupe` and `replay`), `replay-table.py` and the docs
(`README.md`, `docs/recorder.md`, `docs/substitution.md`) would follow, and existing runs' files
would move. The trees can stay where they are, so `cachet-lean`'s readers needn't change. Not
urgent: nothing breaks as it is.

## The recorder: ARM's breakpoint counter (undecided)

Undecided, as of 2026-11-17: this may or may not be wanted. On ARM, a `BKPT`'s immediate is the
number of breakpoints the process emitted before it (`Assembler::as_bkpt`'s `static int hit`,
whose low 16 bits it keeps), so it depends on everything the process compiled earlier. Release
builds have them too: `CacheRegisterAllocator::ensureDoubleRegister` always emits an
`assumeUnreachable`, a bare `breakpoint()` there, behind a jump over it. In the release double
`~` stub (`0a00006e0000016a0101`), `b +8` at 52 and `bkpt` at 56 are the only bytes that vary
between processes. x64's `int3` has no immediate.

The records can't tell Lean that number. The choices:
- **Leave it unrecorded** (now): `cachet-lean` treats `BKPT` immediates as unknown and compares the
  rest exactly. Nothing about behaviour is lost, since a jump always skips them, but a wrong count
  of breakpoints in a stub would go unnoticed.
- **Record it:** each ARM compile record gets the counter when the stub's compilation starts, and
  Lean's `as_bkpt` takes it as an input, as its compiler takes `abiFunctions`. Then:
  - `hit` is local to `as_bkpt`, so the instrumentation moves it to file scope with an accessor,
    inside `#ifdef JS_PHOENIX` (translated code, but builds without Phoenix stay the same);
  - the counter is shared by all threads and isn't atomic, and wasm compiles on helper threads.
    Record the counter at the end too: a record where end minus start isn't its code's number of
    `BKPT`s had another thread's breakpoints interleaved;
  - deduplication keeps the value of the record that gave a line its bytes, out of the key, as for
    `code.abiFunctions`;
  - record format 7, and fresh ARM runs, debug and release.

It depends on whether `cachet-lean` wants to model the process-wide counter or treat the
immediate as a free parameter.

## Substitution: follow-ups

Substitute mode is built ([docs/substitution.md](docs/substitution.md)). Left to do:

- **Compare mode**, deferred on 2026-11-03: check Lean's code against C++'s, instruction by
  instruction, instead of running it. Encodings and lengths differ, so it needs a decoder. The
  compile records' per-op offsets are there to blame a mismatch on one op.
- **Addresses in replayed code.** Ops that call into C++ (`callVM`, `callWithABI`) or embed
  runtime addresses need the process's addresses, which a table can't carry. Lean would have to
  say where they go, for the linker to fill in, or such stubs stay out of tables. C++ code with
  relocations is `refused` today; calls through absolute addresses have no relocations, so they
  aren't detected.
- **Check that substituted code runs**, not only that it's compiled. `corpus.py replay` counts
  compilations; a stub that's attached but never entered again tests nothing. Reading the
  stubs' `enteredCount_` would tell.
- **Substitute only where the table's configuration holds.** Replay refuses to start with IC
  frame pointers on, but otherwise substitutes in any process: one with Spectre mitigations on
  (`--spectre-mitigations=on`: 15 processes of the identity replay run, in `atomics/bigint-*.js`
  and `ion/bug1433496.js`), or one whose test chose other CPU features (`--arm-hwcap` on ARM,
  some without ARMv7; `--no-avx`, `--no-sse4` or `--no-ssse3` on x64, `--no-avx` for every test
  in `wasm/`: see `armFlags` and `x86CPUInfo` in [docs/recorder.md](docs/recorder.md)). C++
  would emit other code there for an op that reads the option or the feature. Harmless while no
  op Lean models reads one (`cachet-lean`'s driver leaves those processes out of its counts).
  Agreed design (2026-11-10): Lean defines which configurations are in scope, with the CPU
  features modeled in the translated compiler, so the scope is explicit; a replay table states
  the configuration it was made for, and the hook substitutes only in processes whose
  `jitOptions`, `armFlags` or `x86CPUInfo` match it.

## Provenance of recorded runs (low priority)

A run's `rev.txt` names the `phoenix-dev` commit it was recorded on, but rebasing `phoenix-dev`
(after a bug fix moves to `phoenix`, or when the pin moves) gives every commit a new ID, so older
runs name commits that are no longer on the branch. For now, re-run what's still needed after a
rebase. Deferred on 2026-11-04.
