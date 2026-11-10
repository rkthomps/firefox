# Backends: building SpiderMonkey for other architectures

`cachet-lean` translates two of SpiderMonkey's JIT backends, x64 and 32-bit ARM, and this machine
is x86_64. A backend other than x64 is cross-compiled here, as Mozilla's CI does: it builds its
Android, Linux and Windows ARM builds on x86_64 Linux workers (`taskcluster/kinds/build/`), and
runs Android's tests on phones. A backend needs:

1. **a sysroot**: the target system's headers and libraries (below);
2. **Rust's standard library for the target**, since the shell links Rust code (below);
3. **a mozconfig** with `--target=…`, in [spidermonkey.md](spidermonkey.md);
4. to run its shell, **a runner**: QEMU's user mode, which runs one program of another
   architecture on this machine (below), or hardware.

The USR index ([usr-index.md](usr-index.md)) needs only the first three: configure, the generated
headers and the compile database. Nothing runs.

| Backend | Sysroot | Rust target | Where it stands (2026-11-09) |
|---|---|---|---|
| x64 | Mozilla's `sysroot-x86_64-linux-gnu`, from `./mach bootstrap` | `x86_64-unknown-linux-gnu` | the recorder, and the index |
| 32-bit ARM | `sysroot.py --arch armhf` (below) | `armv7-unknown-linux-gnueabihf`, installed | the recorder (`obj-js-arm` here, run under QEMU), and the index (`obj-js-arm` in `../firefox-phoenix`, configured only) |

Mozilla publishes sysroots for x86 (`sysroot-i686-linux-gnu`) and arm64 (`sysroot-aarch64-linux-gnu`)
too, which `./mach bootstrap` fetches (`taskcluster/kinds/toolchain/sysroot.yml`). For the other
backends, `sysroot.py` takes Debian's name for the architecture.

## The ARM baseline

Agreed on 2026-11-06: ARMv7-A, Thumb-2, VFPv3-D16 and the hard-float calling convention, Debian
armhf's baseline, which a Raspberry Pi's 32-bit OS has too. Outside Android, configure leaves the
architecture to the compiler, and clang's default for `arm-linux-gnueabihf` is ARMv6
(`__ARM_ARCH 6`), so the mozconfig names each: `--with-arch=armv7-a`, `--with-thumb=yes`,
`--with-fpu=vfpv3-d16`, `--with-float-abi=hard`. Thumb-2 is how the C++ is compiled, including
`ToSignedInteger<int32_t>`'s inline assembly; the JIT emits ARM-state code either way.

## Sysroots

A sysroot is a directory laid out like the root of the target system (`lib/`, `usr/include/`,
`usr/lib/`), holding only the headers and libraries the compiler and linker read: C's and C++'s
headers, some of which differ by architecture (`ucontext_t`'s registers, the sizes of `long` and
`size_t`), the libraries to link against, and the dynamic loader. `--sysroot=DIR` makes
the compiler look there instead of `/`, and a mozconfig's `--with-sysroot=DIR` passes it. Every
compile command of x64's builds already has `--sysroot ~/.mozbuild/sysroot-x86_64-linux-gnu`:
Mozilla builds against an old Debian's files, not the machine's.

Mozilla has none for 32-bit ARM, so `phoenix-tools/sysroot.py` makes one the way Mozilla's
`taskcluster/scripts/misc/build-sysroot.sh` does, from Debian's packages, but without root:

```
phoenix-tools/sysroot.py --arch armhf        # → ~/.mozbuild/sysroot-armhf-linux-gnu
```

Options:

- `--arch ARCH` (required): Debian's name for the architecture (`armhf`, `arm64`, `riscv64`, …);
- `--dist NAME` (default `bookworm`), `--snapshot TIMESTAMP` (default `20230611T210420Z`),
  `--mirror URL` (in place of the snapshot's), `--gcc N` (default: the release's, 12 for
  bookworm);
- `--package NAME`, repeatable: a package to unpack beyond the list below;
- `--keyring FILE`, or `--no-signature-check` (below);
- `-o DIR` (default `~/.mozbuild/sysroot-ARCH-linux-gnu`), and `--force` to replace a sysroot it
  made before. It won't replace a directory without its manifest.

Its tests make a small archive, signed with a key made for the run, and read it through a
`file://` URL: `python3 phoenix-tools/tests/test-sysroot.py`. They need `gpg`.

### How it works

1. **A fixed state of Debian's archive.** `deb.debian.org` serves each release's latest state,
   and drops old files as point releases and security fixes replace them. `snapshot.debian.org`
   keeps every state the archive has been in, by timestamp, so a pinned timestamp gives the same
   files, byte for byte, on every run. The default is the one `build-sysroot.sh` uses for releases
   after buster: Debian 12.0, dated 10 June 2023. Moving it changes the sysroot, like moving the
   pin.
2. **The signature, then the checksums.** It fetches the release's `InRelease`, which Debian
   signs, and checks it with `gpgv` against Debian's keys: `/usr/share/keyrings/` from the
   `debian-archive-keyring` package, or `--keyring`. It needs a good signature, and no bad or
   revoked one. `InRelease` gives the checksum of the architecture's package list
   (`main/binary-ARCH/Packages.xz`), which gives each package's. With `--no-signature-check`, it
   fetches the unsigned `Release` and trusts the HTTPS connection instead.
3. **A fixed list of packages:** `libc6`, `libc6-dev`, `linux-libc-dev`, `libgcc-s1`,
   `libgcc-12-dev`, `libstdc++6`, `libstdc++-12-dev`, `zlib1g` and `zlib1g-dev`. A standalone JS
   build on Linux uses the system's zlib (`system_zlib_default` in `moz.configure`), as x64's
   does, from Mozilla's sysroot; it's the only library it takes from the system. Dependencies
   aren't resolved. Mozilla's sysroots also have the browser's libraries (GTK, X11, …), which the
   shell doesn't need.
4. **Unpacking.** A `.deb` is an `ar` archive whose `data.tar` holds the files to install at `/`.
   It keeps `lib/`, `usr/include/` and `usr/lib/` (but `usr/lib/debug/`), extracted with
   Python's `data` filter, so nothing lands outside the sysroot. Absolute symlinks, such as
   `lib/ld-linux-armhf.so.3 → /lib/arm-linux-gnueabihf/…`, are made relative, or they'd point at
   this machine's files. Empty directories are removed.
5. **A manifest,** `phoenix-sysroot.json` at the sysroot's root: `v`, `arch`, `dist`, the
   release's `version` and `date`, the `mirror`, whether the signature was checked
   (`signatureChecked`), and each package's `name`, `version`, `filename` and `sha256`.
6. **All or nothing.** It builds in a temporary directory next to the output, and moves it into
   place only once it's complete.

On 2026-11-06, `--arch armhf` took about 20 s and made 50 MB, 2,533 files: glibc 2.36-9, Linux's
headers 6.1.27-1, GCC's runtime and libstdc++ 12.2.0-14, and zlib 1.2.13. Our clang compiled and linked a C++20
program against it (strings, vectors, a 64-bit `std::atomic`, `ucontext_t`) for the baseline
above, into an ARM EABI5 executable whose loader is `/lib/ld-linux-armhf.so.3`. Four symlinks in
`usr/lib/gcc/arm-linux-gnueabihf/12/` dangle: `libasan.so`, `libubsan.so`, `libgomp.so` and
`libatomic.so`, whose packages `libgcc-12-dev` depends on but the list leaves out. The shell
uses none of them: configure links libatomic only if 64-bit atomics need it, and ARMv7 has them
as instructions.

Debian's keys weren't installed on this machine on 2026-11-06 (`sudo apt install
debian-archive-keyring` installs them). That run checked the signature with the keyring package
of the same snapshot, which tested the check but proves no more than `--no-signature-check`.

## Running ARM's shell under QEMU

QEMU's user mode (`qemu-user`) runs one ARM program on this machine: it translates the program's
instructions to x86_64's, and its system calls to this kernel's. `qemu-user-binfmt` registers it
with the kernel (`/proc/sys/fs/binfmt_misc/qemu-arm`), so an ARM program starts like any other,
which is how jit-tests start the shell. Two variables tell it the rest:

- `QEMU_LD_PREFIX=~/.mozbuild/sysroot-armhf-linux-gnu`: where the program's loader
  (`/lib/ld-linux-armhf.so.3`) and libraries are. The sysroot has all that the shell links:
  `libc`, `libm`, `libstdc++`, `libgcc_s` and `libz`.
- `QEMU_CPU=cortex-a7`: the core it emulates, whose features the JIT reads at startup
  (`ARMFlags::Init`, from `AT_HWCAP`). The default, `max`, has every feature. On 2026-11-09,
  QEMU 10.2.1 reported:

  | `QEMU_CPU` | Features (`AT_HWCAP`) |
  |---|---|
  | `max` (default) | VFP, VFPv3, VFPv4, NEON, IDIVA, IDIVT, VFPD32, FPHP |
  | `cortex-a7`, `cortex-a15` | VFP, VFPv3, VFPv4, NEON, IDIVA, IDIVT, VFPD32 |
  | `cortex-a9` | VFP, VFPv3, NEON, VFPD32: no IDIV, no VFPv4 |

  The JIT reads VFPv3 (double constants), IDIV (`flexibleDivMod32`) and NEON where ARM's MASM
  chooses code. `ARMHWCAP` overrides what it reads, in any build. The records use `cortex-a7`, a
  Raspberry Pi 2's core (agreed on 2026-11-09), and each process record says what the JIT made of
  it (`armFlags`, in [recorder.md](recorder.md)).

The command is in the README's "Recording one script". Recording `bitnot.js` takes under a
second. Two runs of the same script gave the same stub bytes but for one
heap address: QEMU loads the shell at the same address each time. The shell needs no library
beyond the sysroot's (no `libatomic`, which the simulator's x86 shell links).

**jit-tests under QEMU** (2026-11-09, recording every kind, `record-jit-tests.sh --build arm`):
19 minutes for all of them, 14,144 test runs passing, 5.3 million generator and 964,372 compile
records (8.5 GB). The two trial-inlining tests failed, as on x64, since `--no-ion` stops the
inlining they check. The other 39 failures were timeouts at `jit_test.py`'s 150 s, of 32 tests;
rerun with 1,200 s, all but one passed, the slowest in 384 s, so the script gives each test 600 s.
The one, `baseline/bug1238815.js`, is slow because of the recorder, not QEMU: it passes under
QEMU in 24 s without recording (see its item in [../TODO.md](../TODO.md)).

## The ARM simulator, and why ARM's records come from QEMU

`--enable-simulator=arm` builds a shell for 32-bit x86 whose JIT emits ARM code. The C++ runs
natively; the JIT's code runs in `jit/arm/Simulator-arm.cpp`, an ARM interpreter. It needs no
ARM runner, but its stubs aren't a real ARM shell's (below), so ARM's records come from the real
shell under QEMU. A simulator shell with the recorder was built on 2026-11-09 for the comparison
below, then deleted. Configure accepts the simulator only with a 32-bit x86 target: the C++ and
the ARM code share data structures, so pointers must be 4 bytes on both sides. CI builds it the
same way (`js/src/devtools/automation/variants/arm-sim`, "SM(arm)" on Treeherder).

To build it again: `mozconfig-js-x64`'s options plus `--target=i686-pc-linux-gnu` and
`--enable-simulator=arm`, with objdir `obj-js-armsim`, and Rust's `i686-unknown-linux-gnu`
target (installed). Configure downloads Mozilla's `sysroot-i686-linux-gnu`. To run, it needs the
32-bit runtime libraries (`libc6-i386 lib32stdc++6 lib32gcc-s1 lib32atomic1 lib32z1`, installed
on 2026-11-09); without them, the shell fails with "cannot execute: required file not found",
since `/lib/ld-linux.so.2` is missing.

**CPU features.** The simulator takes them from `ARMHWCAP` or `--arm-hwcap`, or defaults to
ARMv7, VFP, VFPv3, VFPv4, NEON and IDIV with the soft-float calling convention
(`ReadARMHwCapFlags`). A real armhf shell always uses hard-float, which changes how stubs pass
doubles to C++; the simulator needs `hardfp` for it. An explicit `ARMHWCAP` replaces the whole
default set.

### Where its stubs differ from a real ARM shell's

1. **Struct layouts.** i686 aligns a 64-bit member (`uint64_t`, `double`, and `JS::Value`, which
   has no `alignas`) to 4 bytes inside a struct, and ARM's EABI to 8, so a struct with one after
   a 4-byte member is laid out differently, and stubs build field offsets into their code. On
   2026-11-09, at `phoenix` `a80a8d195688`, each `X::offsetOf…()` and `offsetof(…)` named in the
   stub compilers and ARM's MASM (`CacheIRCompiler`, `BaselineCacheIRCompiler`,
   `MacroAssembler{.cpp,.h,-inl.h}`, `arm/MacroAssembler-arm*`, `SharedICHelpers*`) was evaluated
   by clang with each shell's flags for `CacheIRCompiler.cpp`, with access checks off (most are
   private): 204 of 220 are the same in both, and 16 differ:
   - from the alignment: `ArgumentsData::offsetOfArgs` (8 here, 16 on ARM), four of `Realm`'s
     (`offsetOfActiveGlobal`: 60 and 72), `JitZone`'s five stub pointers,
     `StringToAtomCache::offsetOfLastLookups`, and three of `ResumeFromException`'s;
   - from a simulator-only member, `JSContext::simulator_`, 8 bytes before
     `offsetOfRegExpSearcherLastLimit` and `offsetOfWasm`.

   Stubs read them in arguments objects' elements (`loadArgumentsObjectElement`,
   `pushFunApplyArgsObj`), the global (`loadGlobalObjectData`), allocation
   (`checkAllocatorState`, `storeLocalAllocSite`), string concatenation and RegExps (`JitZone`'s
   stubs), and atomizing (`tryFastAtomize`); `ResumeFromException`'s are in the exception
   handler's tail, and `offsetOfWasm` in wasm's code. All are below 4096, the range of LDR's and
   STR's immediate offsets, so a real ARM shell should emit the same instructions with other
   offsets. Not evaluated: the three `Table::…` offsets of Map's and Set's templates, and two of
   `JSContext`'s that exist only under `FUZZING_JS_FUZZILLI`. The structs that computed offsets
   come from (`NativeObject`, `ICStub`, `ICCacheIRStub`, `BaselineFrame`, `JitFrameLayout`,
   `JSFunction`, `JSString`, `Shape`, `ObjectElements`, `ICScript`, `JitScript`, …) are laid out
   the same, and stub data aligns 64-bit fields to `sizeof(uint64_t)` explicitly.
2. **Calls to natives.** Under `JS_SIMULATOR`, `CallNativeFunction` and `CallDOMFunction` have a
   `RawPointerField` for the redirected target (`CacheIROps.yaml`), `CallClassHook`'s and
   `callAnyNativeFunction`'s targets are redirected, and `CallNativeFunction` compiles to other
   code. All come from the Call ICs, whose generators the recorder doesn't hook yet, but whose
   compilations it records, as it does every kind's (`Call.CallScripted` among the first records,
   on 2026-11-09).
3. **Calls into C++** (`callWithABI`) go through a redirection: the same instructions, with
   another address, which differs between builds anyway.

**Measured on records** (2026-11-09): three scripts (`~`, `arguments[0]` and
`encodeURIComponent(s)`), recorded by both shells, the simulator's with
`ARMHWCAP=armv7,vfp,vfpv3,vfpv4,neon,idiva,vfpd32,hardfp` and QEMU's with `cortex-a7`. Of the
eight stubs compiled, every one has the same op offsets, return and failure offsets, and size in
both, and four (`UnaryArith.BitwiseBitNot`, `Compare.Int32`, `UnaryArith.Int32ToNumeric`,
`UnaryArith.Int32Inc`) the same bytes. In the others, the words that differ are `movw`/`movt`
pairs that load an address (a C++ function, static data or the heap), and:
- in `GetProp.ArgumentsObjectArg`, the two layout ones: `ldr r12, [r12, #12]` and
  `ldrd r2, r3, [r12, #8]` in the simulator, `#20` and `#16` on ARM;
- in `Call.CallNative`, whose CacheIR differs (item 2), one instruction: the native's address is
  loaded from the stub's data, `ldr r12, [r9, #32]`, instead of from the function,
  `ldr r12, [r1, #24]`.

## Rust

`rustup target add armv7-unknown-linux-gnueabihf` installs Rust's standard library compiled for
the target, in `~/.rustup/toolchains/<toolchain>/lib/rustlib/`. It's Rust's counterpart of the
sysroot: `rustc` generates any architecture's code, but every Rust program links against `std`,
and `rustup` installs only the host's. The index needs it too, since configure test-compiles Rust
for the target, and stops with "Cannot compile for …" without it. Configure picks the target from
the ARM options: with Thumb-2 and no NEON it's `armv7-…`, with NEON `thumbv7neon-…`.
