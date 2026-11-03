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

## Distinct counts in the corpus summary

`corpus.py dedupe`'s summary gives, per kind, stub name and input types, only how many records
there were. Add how many of them are distinct (the entries of `unique.jsonl`), and perhaps how
many tests and source locations they came from. A row's record count follows how many IC sites
the tests have, so on its own it overstates how much there is to check: `UnaryArith.BitwiseBitNot
int32,int32` is 253 records but only 35 distinct ones.
