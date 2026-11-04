# Branches: the pin, the bug fixes, and the instrumentation

The rules for what may change in Firefox are under "Changes to Firefox" in
[CLAUDE.md](../CLAUDE.md). This file is how the branches hold those changes, and how to move them
forward.

## The structure

```
upstream ──● pin (tag phoenix-pin/<sha>)
           ├─ [BUG FIX] 0001        phoenix: the patched reference
           ├─ [BUG FIX] 0002
           ├─ instrumentation       phoenix-dev
           ├─ tools
           └─ docs
```

- **The pin** is the upstream commit the model translates, `f35f2f1f3ae8`, tagged
  `phoenix-pin/f35f2f1f3ae8`. icarus clones upstream at its constant `MOZ_CENTRAL_REV` (in
  `../icarus/crates/phoenix/build.rs`), so the model is translated from the unpatched pin.
- **`phoenix`** (checked out in `../firefox-phoenix`) is the pin plus `[BUG FIX]` commits and
  nothing else. It only ever gains commits by fast-forward, except when the pin moves.
- **`phoenix-dev`** (checked out in `../firefox-phoenix-dev`) is `phoenix` plus the
  instrumentation, tools and docs. It's always rebased onto `phoenix`, and has no `[BUG FIX]`
  commits of its own. All the work happens here.

So the bug fixes can always be taken on their own: cherry-picked onto upstream, or replayed onto
a new pin, without the instrumentation. And the instrumentation is a small set of commits to
rebase.

## The reference checkout

`../firefox-phoenix` has `phoenix`'s sources, line for line, so it's where the reference is built
and indexed:

- the shells without Phoenix that the bug-fix reports reproduce on (see
  [spidermonkey.md](spidermonkey.md));
- the USR index that `cachet-lean`'s annotations will be checked against (see
  [TODO.md](../TODO.md)). It indexes this checkout, not `phoenix-dev`'s, whose hooks move the
  lines of the files they're in.

Build there, but never edit there. Building only writes gitignored files (objdirs, mozconfigs,
`artifacts/`), and its sources change only when `phoenix` gains a fix or the pin moves. Either
rewrites files there, so don't do it while a build runs in that checkout (see "Pitfalls" in
[spidermonkey.md](spidermonkey.md)).

## Checking it

`phoenix-tools/check-stack.py --worktree` checks that:

1. `phoenix` is a run of `[BUG FIX]` commits on a commit tagged `phoenix-pin/*`;
2. each `[BUG FIX]` commit changes Firefox files, adds one report in `phoenix-docs/bug-fixes/`,
   numbered after the previous one's, names it in its message, and touches nothing else outside
   `phoenix-tools/repro/`;
3. `phoenix` is an ancestor of `phoenix-dev`, and no commit above it is a `[BUG FIX]`;
4. every change from `phoenix` to `phoenix-dev` to Firefox's files is guarded by `JS_PHOENIX`.
   With `--worktree`, this includes the uncommitted changes;
5. `../firefox-phoenix` has no uncommitted changes.

Check 4 reads the text of the changes, so it's a heuristic; the script's docstring says what it
accepts. Run it before committing and after every rebase.

## Adding a bug fix

From `../firefox-phoenix-dev`:

1. Commit the fix on `phoenix-dev`, alone, with its report (see "Changes to Firefox" in
   [CLAUDE.md](../CLAUDE.md)). Stage only the fix's hunks (e.g. `git apply --cached` with a
   patch of them), not the instrumentation around it.
2. Move it to `phoenix`: if the commit sits directly on `phoenix`'s tip,
   `git -C ../firefox-phoenix merge --ff-only <sha>`; otherwise
   `git -C ../firefox-phoenix cherry-pick <sha>`.
3. Rebase `phoenix-dev` onto `phoenix` (`git rebase phoenix`). Git drops the copy of the fix on
   `phoenix-dev`, because `phoenix` now has the same change. Rebasing needs a clean worktree:
   commit the work in progress first, and don't use `git stash`, whose stack is shared with
   other worktrees and sessions.
4. Run `check-stack.py --worktree`.

The report's reproduction needs builds without Phoenix, and its outputs go in the fix's commit,
so get them before step 1. For the output without the fix, run it in `../firefox-phoenix`. For
the output with it, build in `../firefox-phoenix-dev` without `--enable-phoenix`, which compiles
`phoenix`'s code plus the uncommitted fix. After step 3, rebuild the objdirs in
`../firefox-phoenix`.

## Moving to a new upstream pin

`cachet-lean` has to translate the new pin too, so move the pin together with it.

1. Tag where both branches are, so that old runs stay reproducible from their `rev.txt`:
   `phoenix-final/<old-pin>` and `phoenix-dev-final/<old-pin>`. Tag the new pin
   `phoenix-pin/<new-pin>`.
2. In `../firefox-phoenix`, `git rebase --onto <new-pin> phoenix-pin/<old-pin> phoenix`. Drop the
   fixes upstream has made already; each report says how to check. Update the others' upstream
   status, and rerun their reproductions on the new pin.
3. In `../firefox-phoenix-dev`, `git rebase --onto phoenix phoenix-final/<old-pin> phoenix-dev`,
   rebuild, and rerun the recordings. Rebuild the objdirs in `../firefox-phoenix` too.
4. Update `MOZ_CENTRAL_REV` in icarus, and run `check-stack.py --worktree`.
