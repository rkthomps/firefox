# 0004: `CanRelinkJump` subtracts pointers into different objects

- **Where:** SpiderMonkey, `js/src/jit/x86-shared/Patching-x86-shared.h`,
  `X86Encoding::CanRelinkJump`, as called by `Assembler::executableCopy` in
  `js/src/jit/x64/Assembler-x64.cpp`.
- **Since:** at least bug 1125202 (January 2015), which reorganized the x86 assembler and has
  the line as it is now. Its x64 callers are as they've been since bug 1657830 (August 2020).
- **Upstream:** still on `main` at `8cd3db740e1b` (2026-11-18). Not filed.
- **Fixed on `phoenix`** by the commit `[BUG FIX] Subtract addresses, not pointers, in
  CanRelinkJump`.

## What's wrong

`CanRelinkJump` says whether a jump at `from` can reach `to` with a 32-bit displacement:

```cpp
inline bool CanRelinkJump(void* from, void* to) {
  intptr_t offset = static_cast<char*>(to) - static_cast<char*>(from);
  return (offset == static_cast<int32_t>(offset));
}
```

It subtracts two `char*`s. C++ defines that only when both point into the same array
(`[expr.add]` 7.6.6/5, in N4860, C++20):

> (5.1) If P and Q both evaluate to null pointer values, the result is 0.
> (5.2) Otherwise, if P and Q point to, respectively, array elements i and j of the same array
> object x, the expression P - Q has the value i − j.
> (5.3) Otherwise, the behavior is undefined.

Its two callers are in `Assembler::executableCopy`, which copies a finished assembler buffer into
executable memory and links its jumps:
- for `extendedJumps_`, the jumps and calls to absolute addresses (`call(ImmPtr)`, so every
  `callWithABI` with a fixed C++ function), `from` is in the new code and `to` is the target, such
  as a C++ function's address cast to `void*`. A function isn't an object at all, so this is 5.3,
  on every call from JIT code into C++ that x64 links;
- for `codeJumps_` (in a `MOZ_RELEASE_ASSERT`), `from` is in the new code and `to` in other JIT
  code, a different `JitCode` in the process's executable memory. Arguably 5.3 too.

`SetRel32`, in the same file, computes the same difference in a defined way: it converts each
pointer to `intptr_t` with `reinterpret_cast`, which is implementation-defined
(`[expr.reinterpret.cast]` 7.6.1.9/4; Clang gives the address's bits), and subtracts integers.

## What it affects

Every x64 build, on every link of JIT code that calls into C++ or jumps to other JIT code. Only
x64 calls `CanRelinkJump`; x86 includes the header but doesn't.

It isn't observable in practice: Clang compiles the subtraction as an integer subtraction, so the
result is what the fix computes. Nothing is known to misbehave. It matters to tools that give C++
its standard meaning, which stop at undefined behaviour: the Phoenix effort's Lean translation of
x64's linking can't model any stub that calls into C++ while the line is there.

## Reproducing it

The line runs, on pointers into different objects, whenever a baseline IC stub that calls into
C++ is linked. A release shell, without Phoenix:

```
# mozconfig-js-x64-release
ac_add_options --enable-project=js
ac_add_options --disable-debug
ac_add_options --enable-optimize
ac_add_options --as-milestone=release
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-x64-release
```

The script, `phoenix-tools/repro/bitnot-double.js`, attaches the baseline IC stub for `~` of a
double, which truncates it inline and calls `JS::ToInt32` for doubles out of `int32`'s range:

```js
function bitNot(x) {
  return ~x;
}
let n = 0;
for (let i = 0; i < 100; i++) {
  n += bitNot(i + 0.5);
}
print(n, bitNot(1e20));
```

A gdb script, `phoenix-tools/repro/canrelink.gdb`, stops at the line for `extendedJumps_`
(`Assembler-x64.cpp`, the `if (X86Encoding::CanRelinkJump(src, rp.target))` in `executableCopy`)
when the target is `JS::ToInt32`:

```
set pagination off
set breakpoint pending on
break Assembler-x64.cpp:195 if rp.target == (void*)&JS::ToInt32
commands
  silent
  printf "from (src)       = %p\n", src
  printf "to   (rp.target) = %p\n", rp.target
  info symbol rp.target
  info symbol src
  p (long)((char*)rp.target - (char*)src)
  continue
end
run
```

```
gdb -q -batch -x canrelink.gdb --args $OBJDIR/dist/bin/js --no-ion --baseline-eager bitnot-double.js
```

**Without the fix** (observed): the subtraction runs on a pointer into the stub's new code, in
memory no object file maps, and `JS::ToInt32`'s address, in the shell's `.text`, 48 TB apart:

```
from (src)       = 0x29927f856752
to   (rp.target) = 0x555556ee11b0
JS::ToInt32(double) in section .text of …/obj-js-x64-release/dist/bin/js
No symbol matches src.
$1 = 48115837610590
-5050 -1661992961
```

The difference doesn't fit in 32 bits, so the call goes through the extended jump table, as
intended. **With the fix** the script prints the same, `-5050 -1661992961`.

**No sanitizer catches it** (tried). UBSan has no check of what pointers a subtraction is given.
ASan has one, `-fsanitize=pointer-subtract` with `ASAN_OPTIONS=detect_invalid_pointer_pairs=2`,
which instruments the line (`__sanitizer_ptr_sub` is called on it), but it only knows the heap,
the stack and globals, and lets pairs of pointers it knows nothing about through. A shell built
with it (`--enable-address-sanitizer --disable-jemalloc`, and `-fsanitize=pointer-subtract` in
`CFLAGS` and `CXXFLAGS`) ran the script, and printed `-5050 -1661992961`:
- at the line, `__sanitizer_ptr_sub` was called on `JS::ToInt32`'s address and the stub's code
  (`0x5555582cdfc0` and `0x3fb68d746752`, by gdb), and reported nothing;
- it made 57,024 other reports, all of them elsewhere, mostly about SpiderMonkey's own arenas
  (`LifoAlloc`'s chunks, in the frontend), so the check isn't usable on SpiderMonkey as it is.

## The fix

```diff
 inline bool CanRelinkJump(void* from, void* to) {
-  intptr_t offset = static_cast<char*>(to) - static_cast<char*>(from);
+  intptr_t offset =
+      reinterpret_cast<intptr_t>(to) - reinterpret_cast<intptr_t>(from);
   return (offset == static_cast<int32_t>(offset));
 }
```

As `SetRel32` does: the addresses are converted to integers, which is implementation-defined, and
subtracted. The subtraction can't overflow: only x64 calls it, where user-space addresses are
below 2^47 (2^56 with 5-level paging), so both are positive and their difference fits in 64
bits. The narrowing to `int32_t` is defined since C++20 (`[conv.integral]` 7.3.8/3).

**The generated code doesn't change.** `Assembler::executableCopy` in a release build (the
mozconfig above, Mozilla's clang 22.1.8) is 0x13b bytes with and without the fix, and the same
instructions; `CanRelinkJump` is, in both, a 64-bit `sub`, a `movslq` and a `cmp`. The only
differences are the addresses of two crash messages in `.rodata`, which the two builds lay out
differently.

## Other undefined behaviour of the same kind, not changed

Found while fixing this. Neither is on a path the Phoenix effort's translation reaches yet, and,
like this one, neither is known to misbehave, so they're noted here and left until something
needs them:
- **`SetRel32` on 32-bit x86** (`Patching-x86-shared.h`): its `intptr_t` subtraction can overflow
  there, which is undefined (`[expr.pre]` 7.1/4). `intptr_t` is 32 bits, and user-space addresses
  can be above 2 GB (up to 3 GB with a 32-bit kernel, nearly 4 GB for a 32-bit process on a 64-bit
  kernel). E.g. `from = 0x9000'0000` is −0x7000'0000 as an `intptr_t`, and with `to =
  0x2000'0000`, `to - from` is 0x9000'0000, more than `INT32_MAX`. x86's
  `Assembler::executableCopy` calls it on JIT code and C++ addresses. Not checked: whether x86's
  executable memory and the shell can be that far apart in practice. Subtracting `uintptr_t`s
  would be defined, and give the 32-bit displacement the hardware wraps the same way.
- **`GetRel32Target`** (`Patching-x86-shared.h`): `(char*)where + rel` makes a pointer outside the
  code buffer `where` points into whenever the jump's target is in other JIT code or in C++, which
  is undefined (`[expr.add]` 7.6.6/4). Its callers are x86's and x64's relocation iterators
  (`CodeFromJump`, in `Assembler-x86.cpp` and `Assembler-x64.cpp`), which find the `JitCode` a
  jump targets, when tracing.

## How it was verified

On 2026-11-18: without the fix, on `phoenix` at `a80a8d195688` (the pin `f35f2f1f3ae8` plus fixes
0001 to 0003), in `../firefox-phoenix`; with it, on `phoenix-dev` built without Phoenix (the same
plus this fix). Both with the release mozconfig above; the gdb run on the build without the fix.
