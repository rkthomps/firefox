# Backends: building SpiderMonkey for other architectures

`cachet-lean` translates two of SpiderMonkey's JIT backends, x64 and 32-bit ARM, and this machine
is x86_64. A backend other than x64 is cross-compiled here, as Mozilla's CI does: it builds its
Android, Linux and Windows ARM builds on x86_64 Linux workers (`taskcluster/kinds/build/`), and
runs Android's tests on phones. A backend needs:

1. **a sysroot**: the target system's headers and libraries (below);
2. **Rust's standard library for the target**, since the shell links Rust code (below);
3. **a mozconfig** with `--target=…`, in [spidermonkey.md](spidermonkey.md);
4. to run its shell, **a runner**: QEMU's user mode, which runs one program of another
   architecture on this machine, or hardware. None is set up yet.

The USR index ([usr-index.md](usr-index.md)) needs only the first three: configure, the generated
headers and the compile database. Nothing runs.

| Backend | Sysroot | Rust target | Where it stands (2026-11-06) |
|---|---|---|---|
| x64 | Mozilla's `sysroot-x86_64-linux-gnu`, from `./mach bootstrap` | `x86_64-unknown-linux-gnu` | the recorder, and the index |
| 32-bit ARM | `sysroot.py --arch armhf` (below) | `armv7-unknown-linux-gnueabihf`, installed | `mozconfig-js-arm` configured in `../firefox-phoenix`, not built, and the index |

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

## Rust

`rustup target add armv7-unknown-linux-gnueabihf` installs Rust's standard library compiled for
the target, in `~/.rustup/toolchains/<toolchain>/lib/rustlib/`. It's Rust's counterpart of the
sysroot: `rustc` generates any architecture's code, but every Rust program links against `std`,
and `rustup` installs only the host's. The index needs it too, since configure test-compiles Rust
for the target, and stops with "Cannot compile for …" without it. Configure picks the target from
the ARM options: with Thumb-2 and no NEON it's `armv7-…`, with NEON `thumbv7neon-…`.
