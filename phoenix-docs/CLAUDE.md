# Phoenix: testing the Lean CacheIR model against SpiderMonkey

This file is loaded from the worktree's top-level `CLAUDE.md`. Firefox's own instructions
(`AGENTS.md`) still apply. This file adds what's specific to the Phoenix effort.

## What this is

`../cachet-lean` is a Lean 4 translation of SpiderMonkey's baseline IC pipeline (stub generator →
CacheIR → MASM → assembly → machine code, on x64 and 32-bit ARM), plus the interpreter operations
that stubs are proved against. The branch `phoenix-dev` builds infrastructure that tests that
model against the real SpiderMonkey. The goals are in [GOAL.md](GOAL.md), and how feasible each one
is, with the proposed strategies, is in [FEASIBILITY.md](FEASIBILITY.md).

## Checkouts and branches

- `../firefox`: tracks upstream `main`. Don't develop here.
- The **pin**: the upstream commit `f35f2f1f3ae8`, tagged `phoenix-pin/f35f2f1f3ae8`. It's what
  `cachet-lean` translates: icarus clones upstream at its constant `MOZ_CENTRAL_REV` (in
  `../icarus/crates/phoenix/build.rs`).
- `../firefox-phoenix`: branch `phoenix`, the pin plus `[BUG FIX]` commits and nothing else: the
  patched reference. Don't work in this checkout: make fixes in `phoenix-dev` and move them (see
  "Branches" below).
- `../firefox-phoenix-dev` (this worktree): branch `phoenix-dev`, which is `phoenix` plus the
  instrumentation, docs and tools, always rebased onto `phoenix`. All infrastructure goes here.
- `../cachet-lean`: the model. It has its own `CLAUDE.md` with stricter rules: discuss a design
  before writing it, wait for an explicit go-ahead before editing, and edit only with the Edit or
  Write tools. Follow them whenever you touch that repository.

## Reading the model

`cachet-lean` is thoroughly documented. Don't rediscover what it already says:

- `../cachet-lean/CLAUDE.md`: guiding principles (faithful to the ISA manuals and to the C++
  standard), the verification strategy, and a map of `docs/arch/`.
- `../cachet-lean/docs/arch/plan.md`: where the work stands and what's next.
- `../cachet-lean/docs/arch/layers.md`: what each layer covers so far.
- The layer files (`x64/`, `arm/`, `masm/`, `cacheir/`, `generator/`, `vm/`): read the one for the
  layer a test exercises before writing the test.

## SpiderMonkey code this effort hooks into

The tree is pinned and older than Searchfox's index, so read the exact code locally, restricted to
`js/src/jit` or another narrow directory, rather than through `searchfox-cli`.

- `js/src/jit/BaselineCacheIRCompiler.cpp`: `LookupOrCompileStub` compiles a stub's CacheIR or
  finds its code in the `JitZone`'s cache. The cache key is the kind and the CacheIR bytes, so
  baseline stub code doesn't depend on the heap.
- `js/src/jit/BaselineIC.cpp`: the fallback stubs (`DoUnaryArithFallback`, …), where an IC's inputs
  reach its generator.
- `js/src/jit/CacheIR.cpp`, `CacheIRGenerator.h`: the stub generators (`UnaryArithIRGenerator`, …).
- `js/src/jit/CacheIRSpewer.cpp` (`SpewCacheIROpsAsAOT`) and `js/src/jit/CacheIRAOT.h`: the
  ahead-of-time IC corpus. It serializes a `CacheIRWriter` as portable text and rebuilds a writer
  from it. `--enable-aot-ics` builds only with the portable baseline interpreter, so don't put it
  in a JIT mozconfig (see [FEASIBILITY.md](FEASIBILITY.md)).
- `js/src/jit/x86-shared/AssemblerBuffer-x86-shared.h`: with `IONFLAGS=codegen` in a `JS_JITSPEW`
  build (debug builds have it), every x86 instruction is printed with its offset as it's emitted.

## Builds

- The browser objdir is `obj-x86_64-pc-linux-gnu` (debug, from `./mozconfig`). It doesn't contain a
  JS shell: `--enable-js-shell` is on by default only for standalone JS builds.
- For jit-tests and recording stubs, use a separate JS-shell objdir and select it with `MOZCONFIG`
  (see "Builds" in [FEASIBILITY.md](FEASIBILITY.md)).
- `./mach build && ./mach ide vscode --no-interactive` rebuilds and refreshes clangd's database.

## Conventions for this effort

- Put documents for this effort in `phoenix-docs/`, and keep [GOAL.md](GOAL.md) and
  [FEASIBILITY.md](FEASIBILITY.md) current when a goal or plan changes.
- Keep the hooks small and in few places, so that rebasing `phoenix-dev` onto a newer pin stays
  cheap.
- Corpora and test outputs (recorded stubs, traces, run logs) go outside the source tree, or in a
  gitignored directory, never into `js/src`.

## Changes to Firefox: instrumentation or bug fixes, nothing else

Differential results mean nothing if the reference has drifted, and one day this work has to run
on upstream Firefox and be discussed with its developers. So every edit to Firefox's own files
(anything outside `phoenix-docs/` and `phoenix-tools/`) is one of two kinds. Ad hoc edits are not
allowed: no refactors, cleanups or drive-by fixes outside these two. The one exception is the
top-level `CLAUDE.md`, which loads this file and isn't built.

1. **Instrumentation**, compiled only with `--enable-phoenix`:
   - code inside `#ifdef JS_PHOENIX`, or a guard that names `JS_PHOENIX` (e.g.
     `#if defined(ENABLE_JS_AOT_ICS) || defined(JS_PHOENIX)`);
   - files built only under `CONFIG["JS_PHOENIX"]`;
   - the `--enable-phoenix` option itself in `js/moz.configure`.

   With the flag off, `phoenix-dev` must build the same code as `phoenix`, so even a
   behaviour-neutral rewrite of existing code (naming a temporary, reordering) belongs inside
   the `#ifdef`. `rg JS_PHOENIX js/` then lists every hook. Instrumentation is also off at run
   time unless its environment variable (`PHOENIX_RECORD`, …) is set.

2. **Bug fixes**: any change that's active without `--enable-phoenix`, i.e. that changes
   SpiderMonkey's code or behaviour in some build. It's allowed only to fix a real bug in
   SpiderMonkey that upstream should fix too. If instrumentation seems to need such a change and
   it isn't a bug, put it behind `JS_PHOENIX` instead, or ask. Each bug fix:
   - is **its own commit**, containing only the fix and its report: no instrumentation, no
     unrelated docs. One bug per commit;
   - has a commit message whose subject starts with **`[BUG FIX]`**, e.g. `[BUG FIX] Handle
     ArrayBufferViewKind::Immutable in the AOT IC spewer`. Its body says what's wrong, how to
     reproduce it, what the fix does, whether it's still on upstream `main`, and names the
     report;
   - has a **report** in `phoenix-docs/bug-fixes/NNNN-short-name.md`, numbered in order. It must
     let a Firefox developer reproduce the bug without any Phoenix code:
     - what's wrong, and which builds and configurations it affects;
     - a reproduction on an upstream-shaped build: the mozconfig, the exact command and script
       (scripts go in `phoenix-tools/repro/`), and the observed and expected output. Run it, on
       a build without the fix and on one with it, and paste what you saw;
     - the fix, and why it's correct;
     - its upstream status: still on `main` (at which revision), filed as a bug, or fixed
       upstream (by which bug);
   - goes on **`phoenix`**, below all the instrumentation, so it can be cherry-picked onto
     upstream on its own (see "Branches").

   `git log --grep='^\[BUG FIX\]' phoenix` and `phoenix-docs/bug-fixes/` are the list of changes
   that must be carried to upstream, or filed there. Filing a bug in Bugzilla or sending a patch
   upstream is outward-facing: ask first.

   **A fix to code the model translates** (a generator, the CacheIR compiler, MASM, an
   assembler, an interpreter operation) changes what the model has to match, and icarus clones
   the unpatched pin. Ask before putting such a fix on `phoenix`: either `cachet-lean` and icarus
   follow `phoenix`, or the fix waits for upstream. The fixes so far are all outside translated
   code.

## Branches

```
upstream ──● pin (tag phoenix-pin/<sha>)
           ├─ [BUG FIX] 0001        phoenix: the patched reference
           ├─ [BUG FIX] 0002
           ├─ instrumentation       phoenix-dev
           └─ docs, tools
```

- `phoenix` only ever gains `[BUG FIX]` commits, by fast-forward, except when the pin moves.
- `phoenix-dev` is always rebased onto `phoenix`, and has no `[BUG FIX]` commits of its own.
- **Check the structure** with `phoenix-tools/check-stack.py --worktree` before committing, and
  after any rebase. It checks both branches, and that every change from `phoenix` to
  `phoenix-dev` (committed and, with `--worktree`, uncommitted) is guarded by `JS_PHOENIX`.

**Adding a bug fix**, from this worktree:

1. Commit the fix on `phoenix-dev`, alone, as described above. Stage only the fix's hunks (e.g.
   `git apply --cached` with a patch of them), not the instrumentation around it.
2. Move it to `phoenix`: if the commit sits directly on `phoenix`'s tip,
   `git -C ../firefox-phoenix merge --ff-only <sha>`; otherwise
   `git -C ../firefox-phoenix cherry-pick <sha>`.
3. Rebase `phoenix-dev` onto `phoenix` (`git rebase phoenix`). Git drops the copy of the fix on
   `phoenix-dev` because `phoenix` now has the same change. Rebasing needs a clean worktree:
   commit the work in progress first, and don't use `git stash`, whose stack is shared with
   other worktrees and sessions.
4. Run `check-stack.py --worktree`.

**Moving to a new upstream pin** (with `cachet-lean`, which has to translate the new pin too):

1. Tag where both branches are, so old runs stay reproducible from their `rev.txt`:
   `phoenix-final/<old-pin>` and `phoenix-dev-final/<old-pin>`. Tag the new pin
   `phoenix-pin/<new-pin>`.
2. In `../firefox-phoenix`, `git rebase --onto <new-pin> phoenix-pin/<old-pin> phoenix`. Drop
   the fixes upstream has made already; each report says how to check. Update the others'
   upstream status, and rerun their reproductions on the new pin.
3. Here, `git rebase --onto phoenix phoenix-final/<old-pin> phoenix-dev`, rebuild, and rerun the
   recordings.
4. Update `MOZ_CENTRAL_REV` in icarus, and run `check-stack.py --worktree`.
