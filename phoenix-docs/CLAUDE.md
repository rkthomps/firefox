# Phoenix: testing the Lean CacheIR model against SpiderMonkey

This file is loaded from the worktree's top-level `CLAUDE.md`. Firefox's own instructions
(`AGENTS.md`) still apply. This file adds what's specific to the Phoenix effort: where things
are, what to know before working, the rules for changing Firefox, and how to keep these docs up
to date.

## What this is

`../cachet-lean` is a Lean 4 translation of SpiderMonkey's baseline IC pipeline (stub generator →
CacheIR → MASM → assembly → machine code, on x64 and 32-bit ARM), plus the interpreter operations
that stubs are proved against. The branch `phoenix-dev` builds infrastructure that tests that
model against the real SpiderMonkey.

## Where things are

**This directory** (`phoenix-docs/`):

- [README.md](README.md): what's here, and how to build and run the tools.
- [GOAL.md](GOAL.md): the four testing goals.
- [TODO.md](TODO.md): work deferred for later.
- [bug-fixes/](bug-fixes/): one report per `[BUG FIX]` commit.
- [docs/feasibility.md](docs/feasibility.md): how feasible each goal is, the plan, and where
  it stands.
- [docs/recorder.md](docs/recorder.md): how the recorder works, and its record format.
- [docs/substitution.md](docs/substitution.md): replacing stub code with Lean's (`PHOENIX_REPLAY`),
  the replay table's format, and what the Lean driver has to produce.
- [docs/usr-index.md](docs/usr-index.md): the USR index of the reference's C++ entities, for
  `cachet-lean`'s provenance checker: running it, its dependencies, and its format.
- [docs/backends.md](docs/backends.md): cross-compiling SpiderMonkey for other architectures:
  sysroots (`sysroot.py`), Rust's targets, and the ARM baseline.
- [docs/branches.md](docs/branches.md): the branches, and how to add a bug fix, move the pin, or
  push them.
- [docs/spidermonkey.md](docs/spidermonkey.md): the SpiderMonkey code this effort hooks into,
  the builds, and their pitfalls.

**Tools** are in `phoenix-tools/`: `record-jit-tests.sh`, `corpus.py`, `replay-table.py`,
`usr-index.py`, `sysroot.py` and `check-stack.py`, tests in `tests/`, the bug reports' scripts in
`repro/`, libclang's Python bindings in `third_party/clang/`, and `pyproject.toml` and `uv.lock`
for type-checking them. Recorded runs go in `../phoenix-runs/`, indexes in `../phoenix-index/`,
and sysroots in `~/.mozbuild/`.
What the tools need is in the README's "Dependencies".

**Checkouts and branches** (details in [docs/branches.md](docs/branches.md)):

- `../firefox`: tracks upstream `main`. Don't develop here.
- The **pin**, `f35f2f1f3ae8`, tagged `phoenix-pin/f35f2f1f3ae8`: the upstream commit
  `cachet-lean` translates.
- `../firefox-phoenix`: branch `phoenix`, the pin plus `[BUG FIX]` commits and nothing else. It's
  the reference checkout: build there (shells without Phoenix, the USR index), but never edit
  there. Make fixes in `phoenix-dev` and move them.
- `../firefox-phoenix-dev` (this worktree): branch `phoenix-dev`, `phoenix` plus the
  instrumentation, tools and docs, always rebased onto `phoenix`. All infrastructure goes here.
- `../cachet-lean`: the model. It has its own `CLAUDE.md` with stricter rules: discuss a design
  before writing it, wait for an explicit go-ahead before editing, and edit only with the Edit or
  Write tools. Follow them whenever you touch that repository.

**The model** is thoroughly documented. Don't rediscover what it already says:

- `../cachet-lean/CLAUDE.md`: guiding principles (faithful to the ISA manuals and to the C++
  standard), the verification strategy, and a map of `docs/arch/`.
- `../cachet-lean/docs/arch/plan.md`: where the work stands and what's next.
- `../cachet-lean/docs/arch/layers.md`: what each layer covers so far.
- The layer files (`x64/`, `arm/`, `masm/`, `cacheir/`, `generator/`, `vm/`): read the one for the
  layer a test exercises before writing the test.

## Essentials

- **Edit files with the Edit or Write tool**, never with shell commands (`sed`, heredocs,
  scripts), even in auto mode, where those are allowed. This covers every file you change by
  hand: Firefox's sources, `phoenix-tools/`, `phoenix-docs/`, and the mozconfigs. The user
  inspects each change in the IDE panel, and edits made through the shell don't show there.
  Moving, renaming or deleting a whole file is different: use `git mv` or `git rm` for a tracked
  file, and `mv` or `rm` for an untracked one. Files that tools and builds write (records, logs,
  objdirs) aren't edits.
- **Read SpiderMonkey's code locally**, restricted to `js/src/jit` or another narrow directory:
  the tree is pinned and older than Searchfox's index.
- **Type-check the Python tools.** Every function in `phoenix-tools/` (but `third_party/`) has
  type annotations, and `uv run pyright` in `phoenix-tools/` must report no errors, in standard
  mode (configured in `pyproject.toml`). Run it before committing a change to the tools. They
  stay flat scripts, run as files, not a package.
- **Build in the right objdir.** The JS shell with the recorder is `obj-js-x64`
  (`MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build`). Shells without Phoenix are built in
  `../firefox-phoenix`. The browser objdir has no shell. All the objdirs, and which checkout
  has each, are in [docs/spidermonkey.md](docs/spidermonkey.md).
- **Don't edit a source file while a build is running**: later builds can silently keep the old
  code (see "Pitfalls" in [docs/spidermonkey.md](docs/spidermonkey.md)).
- **Run `phoenix-tools/check-stack.py --worktree`** before committing and after every rebase.
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
     upstream on its own. How to move it there is in [docs/branches.md](docs/branches.md).

   `git log --grep='^\[BUG FIX\]' phoenix` and `phoenix-docs/bug-fixes/` are the list of changes
   that must be carried to upstream, or filed there. Filing a bug in Bugzilla or sending a patch
   upstream is outward-facing: ask first.

   **A fix to code the model translates** (a generator, the CacheIR compiler, MASM, an
   assembler, an interpreter operation) changes what the model has to match, and icarus clones
   the unpatched pin. Ask before putting such a fix on `phoenix`: either `cachet-lean` and icarus
   follow `phoenix`, or the fix waits for upstream. The fixes so far are all outside translated
   code.

## Keeping the docs up to date

Update the docs in the same piece of work as the change they describe, not later.

- **One home per fact.** Each topic has one file; elsewhere, link to it instead of repeating it.
  Where things go:
  - a change to a goal: [GOAL.md](GOAL.md);
  - plans, what's feasible, and where each goal stands: [docs/feasibility.md](docs/feasibility.md);
  - work deferred for later: an item in [TODO.md](TODO.md). Remove the item when it's done;
  - how to build and run something: [README.md](README.md);
  - how a piece of infrastructure works, and its formats: its own file in `docs/`, like
    [docs/recorder.md](docs/recorder.md). A new piece gets a new file, listed in this file's
    "Where things are" and in the README;
  - a bug fix: its report in `bug-fixes/`, committed with the fix (see above);
  - the branches and their workflows: [docs/branches.md](docs/branches.md);
  - SpiderMonkey code, builds, and pitfalls met along the way:
    [docs/spidermonkey.md](docs/spidermonkey.md);
  - instructions for agents: this file.
- **Keep what's documented true.** When a tool's options, a record's fields or a mozconfig
  change, update the doc that describes them. A change to the record format also bumps the
  records' `v`.
- **Date claims about status** ("as of 2026-11-03"), since they go stale.
- **Keep links working.** Links are relative. After moving or renaming a file, search
  `phoenix-docs/`, `phoenix-tools/` and the code comments for its old name.
- **Docs are committed on `phoenix-dev`**, in commits of their own, except a bug fix's report,
  which goes in the fix's commit on `phoenix`.
