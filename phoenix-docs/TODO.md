# TODO

Work deferred for later. Remove an item when it's done.

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

## The USR index for ARM

The USR index is built for x64 only, as of 2026-11-04 ([docs/usr-index.md](docs/usr-index.md)).
ARM needs a configuration of its own, and each option has a catch:

- **The ARM simulator** (`--enable-simulator=arm`, the planned `mozconfig-js-armsim` in
  [docs/spidermonkey.md](docs/spidermonkey.md)) compiles `js/src/jit/arm/`, but for a 32-bit x86
  host. It defines `__i386__`, not `__arm__`, so its index misses the code under
  `#if defined(__arm__)`, such as ARM's inline-assembly `ToSignedInteger<int32_t>`, which
  `cachet-lean`'s `docs/arch/provenance.md` cites. A 32-bit x86 configuration hasn't been tried
  here.
- **A real ARM target** (`--target=arm-linux-gnueabihf`) defines `__arm__`, but configure needs
  an ARM sysroot, and `~/.mozbuild` has only x86_64 and wasm32 ones.

Either way, the index needs only a compile database, not a build: configure, then
`./mach build pre-export export` for the generated headers, then
`./mach build-backend -b CompileDB`, as `../icarus/crates/phoenix/build.rs` does.

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

## Provenance of recorded runs (low priority)

A run's `rev.txt` names the `phoenix-dev` commit it was recorded on, but rebasing `phoenix-dev`
(after a bug fix moves to `phoenix`, or when the pin moves) gives every commit a new ID, so older
runs name commits that are no longer on the branch. For now, re-run what's still needed after a
rebase. Deferred on 2026-11-04.
