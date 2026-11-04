# SpiderMonkey: the code Phoenix hooks into, and how to build it

## Reading the code

The tree is pinned (see [branches.md](branches.md)) and older than Searchfox's index, so read
the exact code locally, restricted to `js/src/jit` or another narrow directory, rather than
through `searchfox-cli`.

## Code this effort hooks into

- `js/src/jit/BaselineCacheIRCompiler.cpp`: `LookupOrCompileStub` compiles a stub's CacheIR or
  finds its code in the `JitZone`'s cache. The cache key is the kind and the CacheIR bytes, so
  baseline stub code doesn't depend on the heap. Compile records and substitution hook in here,
  and `compile()` notes where each op's code begins (see [substitution.md](substitution.md)).
- `js/src/jit/BaselineIC.cpp`: the fallback stubs (`DoUnaryArithFallback`, …), where an IC's
  inputs reach its generator, and `TryAttachStub`, where the recorder hooks in (see
  [recorder.md](recorder.md)).
- `js/src/jit/CacheIR.cpp`, `CacheIRGenerator.h`: the stub generators (`UnaryArithIRGenerator`,
  …).
- `js/src/jit/CacheIRSpewer.cpp` (`SpewCacheIROpsAsAOT`) and `js/src/jit/CacheIRAOT.h`: the
  ahead-of-time IC corpus. It serializes a `CacheIRWriter` as portable text and rebuilds a writer
  from it. `--enable-aot-ics` builds only with the portable baseline interpreter, so don't put it
  in a JIT mozconfig (see "What SpiderMonkey already provides" in
  [feasibility.md](feasibility.md)).
- `js/src/jit/x86-shared/AssemblerBuffer-x86-shared.h`: with `IONFLAGS=codegen` in a `JS_JITSPEW`
  build (debug builds have it), every x86 instruction is printed with its offset as it's
  emitted.

## Builds

Each build has its own objdir, chosen with `MOZCONFIG`, e.g.
`MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build`. `mozconfig*` files are gitignored, so recreate
them from here in a new worktree.

| Objdir | Mozconfig | What it's for |
|---|---|---|
| `obj-x86_64-pc-linux-gnu` | `mozconfig` | the browser (debug). It has no JS shell: `--enable-js-shell` is on by default only for standalone JS builds |
| `obj-js-x64` | `mozconfig-js-x64` | the JS shell with the recorder: jit-tests, the x64 corpus, and later substitution |
| `obj-js-pbl` | `mozconfig-js-pbl` | upstream's AOT IC build (the portable baseline interpreter), without Phoenix: reproducing the bugs in [bug-fixes/](../bug-fixes/) |
| `obj-js-armsim` | `mozconfig-js-armsim` | not built yet: ARM stub bytes on this machine, through SpiderMonkey's ARM simulator |

```
# mozconfig-js-x64
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --enable-phoenix
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-x64
```

```
# mozconfig-js-pbl
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --enable-portable-baseline-interp
ac_add_options --enable-aot-ics
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-pbl
```

```
# mozconfig-js-armsim (planned; needs a 32-bit x86 host build)
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --target=i686-pc-linux-gnu
ac_add_options --enable-simulator=arm
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-armsim
```

- A PBL shell runs PBL only with `--portable-baseline`, and AOT ICs only with `--aot-ics`.
- `./mach build && ./mach ide vscode --no-interactive` rebuilds the browser objdir and refreshes
  clangd's database.

## Pitfalls

- **Don't edit a source file while a build is running.** The compiler can read the old content
  and write the object file after the edit, so the object ends up newer than the source, and
  later builds silently keep the old code. If it happened, `touch` the file and rebuild. A crash
  at a line number that doesn't match the source is the symptom.
- **`./mach format` fails** on this machine: its lint virtualenv can't install its pinned
  packages under Python 3.14. Run the bootstrapped clang-format directly instead:
  `~/.mozbuild/clang-tools/clang-tidy/bin/clang-format -i <files>`.
