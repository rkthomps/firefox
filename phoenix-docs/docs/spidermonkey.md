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
- `js/src/jit/CacheIRWriter.h`: the writer, whose buffer and state the records hold (see
  [recorder.md](recorder.md)).
- `js/src/jit/CacheIRSpewer.cpp` (`SpewCacheIROpsAsAOT`) and `js/src/jit/CacheIRAOT.h`: the
  ahead-of-time IC corpus. It serializes a `CacheIRWriter` as portable text and rebuilds a writer
  from it. Records held that text until version 4 (2026-11-10); nothing of Phoenix uses it now,
  but the three bugs in [bug-fixes/](../bug-fixes/) are in it. `--enable-aot-ics` builds only
  with the portable baseline interpreter, so don't put it in a JIT mozconfig (see "What
  SpiderMonkey already provides" in [feasibility.md](feasibility.md)).
- `js/src/jit/x86-shared/AssemblerBuffer-x86-shared.h`: with `IONFLAGS=codegen` in a `JS_JITSPEW`
  build (debug builds have it), every x86 instruction is printed with its offset as it's
  emitted.

## Builds

Each build has its own objdir, chosen with `MOZCONFIG`, e.g.
`MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build`. `mozconfig*` files are gitignored, so recreate
them from here in a new worktree. An objdir belongs to the checkout it was built from: it records
the source directory's absolute path, so it can't be moved to another checkout, only rebuilt
there.

Builds with Phoenix are in `../firefox-phoenix-dev`. Builds without it are in the reference
checkout, `../firefox-phoenix` (see [branches.md](branches.md)), whose sources are `phoenix`'s.

| Checkout | Objdir | Mozconfig | What it's for |
|---|---|---|---|
| `firefox-phoenix-dev` | `obj-js-x64` | `mozconfig-js-x64` | the JS shell with the recorder: jit-tests, the x64 corpus, substitution |
| `firefox-phoenix-dev` | `obj-x86_64-pc-linux-gnu` | `mozconfig` | the browser (debug), and clangd's database. It has no JS shell: `--enable-js-shell` is on by default only for standalone JS builds |
| `firefox-phoenix` | `obj-js-x64` | `mozconfig-js-x64`, without `--enable-phoenix` | a plain x64 shell of the reference: reproducing a JIT bug without Phoenix, and the compile database of the x64 USR index ([usr-index.md](usr-index.md)) |
| `firefox-phoenix` | `obj-js-pbl` | `mozconfig-js-pbl` | upstream's AOT IC build (the portable baseline interpreter): reproducing the bugs in [bug-fixes/](../bug-fixes/) |
| `firefox-phoenix` | `obj-js-arm` | `mozconfig-js-arm` | a real 32-bit ARM shell, cross-compiled ([backends.md](backends.md)). Configured, not built: the compile database of the ARM USR index |
| `firefox-phoenix-dev` | `obj-js-arm` | `mozconfig-js-arm` | a real 32-bit ARM shell with the recorder, cross-compiled and run under QEMU's user mode: ARM's records ([backends.md](backends.md)) |

```
# mozconfig-js-x64 (firefox-phoenix's is the same without --enable-phoenix, which phoenix lacks)
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
# mozconfig-js-arm: a real 32-bit ARM shell, cross-compiled (phoenix-docs/docs/backends.md).
# firefox-phoenix's is the same without --enable-phoenix
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --enable-phoenix
ac_add_options --target=arm-linux-gnueabihf
ac_add_options --with-arch=armv7-a
ac_add_options --with-thumb=yes
ac_add_options --with-fpu=vfpv3-d16
ac_add_options --with-float-abi=hard
ac_add_options --with-sysroot=$HOME/.mozbuild/sysroot-armhf-linux-gnu
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-arm
```

- A PBL shell runs PBL only with `--portable-baseline`, and AOT ICs only with `--aot-ics`.
- `./mach build && ./mach ide vscode --no-interactive`, in `../firefox-phoenix-dev`, rebuilds the
  browser objdir and refreshes clangd's database.

## Pitfalls

- **Don't edit a source file while a build is running.** The compiler can read the old content
  and write the object file after the edit, so the object ends up newer than the source, and
  later builds silently keep the old code. If it happened, `touch` the file and rebuild. A crash
  at a line number that doesn't match the source is the symptom.
- **`./mach format` fails** on this machine: its lint virtualenv can't install its pinned
  packages under Python 3.14. Run the bootstrapped clang-format directly instead:
  `~/.mozbuild/clang-tools/clang-tidy/bin/clang-format -i <files>`.
