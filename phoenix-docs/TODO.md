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
