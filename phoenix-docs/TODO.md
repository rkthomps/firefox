# TODO

Work deferred for later. Remove an item when it's done.

## The AOT spewer crashes on operands no op reads

Found on 2026-11-03, deferred. With every kind recorded (`PHOENIX_RECORD` without
`PHOENIX_RECORD_KINDS`), the shell hits `MOZ_CRASH(ElementAt(aIndex = 0, aLength = 0))` at the
first compiled `Call` stub (`NewArrayIterator`, in `phoenix-tools/tests/bitnot.js`). Compile
records call `SpewCacheIROpsAsAOT`, which prints `writer.operandLastUsed(i)` for every
`i < numOperandIds()`. But `CacheIRWriter::operandLastUsed_` only grows when an op reads an
operand (`writeOperandId`), so an operand no op reads, such as a `Call` stub's `argc`, is past its
end. Runs limited to `UnaryArith` are unaffected.

It's upstream's bug, latent there for the same reason as
[bug-fixes/0001](bug-fixes/0001-aot-spewer-typeofeq-operand.md), probably. Fix it as a
`[BUG FIX]`, with a report and a PBL reproduction. First decide what to print for an operand that's
never read, so that the AOT loader (`CacheIRAOT.cpp`) rebuilds an equivalent writer: there,
`operandIsDead` must stay false for it.

## Branches: follow-ups

The branch structure is in place (see [docs/branches.md](docs/branches.md)). Left to do:

- Scripts for the two workflows, moving a fix to `phoenix` and moving the pin, if doing them by
  hand gets tedious.
- A way for icarus to clone `phoenix` from the fork (`rkthomps/firefox`) instead of the upstream
  pin, for the day a fix touches code the model translates. `PHOENIX_MOZ_REV` already chooses
  the revision; the clone URL is hard-coded.
- A list of retired fixes: when the pin moves, a fix upstream has made is dropped, and its report
  goes with it (still reachable from the `phoenix-final/<old-pin>` tag).
- The branches and the pin tag are only local. Decide whether to push them to `origin`. Rebasing
  `phoenix-dev` means force-pushing it.

## A USR index for `cachet-lean`'s annotations

`cachet-lean` will annotate each Lean function with the clang USR and the `file:line` of the C++
function it translates, to be checked against an index of SpiderMonkey's sources. Build the index
from the reference checkout, `../firefox-phoenix`, whose sources are `phoenix`'s line for line
(see [docs/branches.md](docs/branches.md)), with a script in `phoenix-tools/`. It will use libclang
from `~/.mozbuild/clang`, over a `compile_commands.json` that `./mach build-backend -b CompileDB`
writes from a gitignored mozconfig. It will also need a 32-bit ARM-simulator configuration
(`--enable-simulator=arm`) for `js/src/jit/arm/`. Design it with `cachet-lean`.

`../icarus/crates/phoenix` already does most of this, for the stub generators:

- `build.rs` makes the compile database of a JS-shell-only debug build without compiling
  anything: `mach configure`, then `mach build pre-export export` for the generated headers, then
  `mach build-backend -b CompileDB`;
- `src/clang_utils.rs` parses a file through libclang (the `clang` crate) with its flags from the
  compile database;
- `src/cpp_subset.rs` identifies C++ functions by their USR.

## Distinct counts in the corpus summary

`corpus.py dedupe`'s summary gives, per kind, stub name and input types, only how many records
there were. Add how many of them are distinct (the entries of `unique.jsonl`), and perhaps how
many tests and source locations they came from. A row's record count follows how many IC sites
the tests have, so on its own it overstates how much there is to check: `UnaryArith.BitwiseBitNot
int32,int32` is 253 records but only 35 distinct ones.

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
