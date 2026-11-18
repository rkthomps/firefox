# 0005: `ProcessExecutableMemory` compares pointers to unrelated objects

- **Where:** SpiderMonkey, `js/src/jit/ProcessExecutableMemory.cpp`, the class
  `ProcessExecutableMemory`: `containsAddress`, which `AddressIsInExecutableMemory` calls, and
  `assertValidAddress`.
- **Since:** `assertValidAddress`'s comparison since bug 1334933 (February 2017), which added the
  pre-reserved executable region; `containsAddress` since bug 1657830 (August 2020), whose x64
  assembler calls it for every jump to a fixed address.
- **Upstream:** still on `main` at `a48ec7d01f11` (2026-11-18). Not filed.
- **Fixed on `phoenix`** by the commit `[BUG FIX] Compare addresses, not pointers, in
  ProcessExecutableMemory`.

## What's wrong

The process's JIT code is all allocated in one region, reserved at startup, which starts at
`base_`. Two methods check whether a pointer is in it:

```cpp
  void assertValidAddress(void* p, size_t bytes) const {
    MOZ_RELEASE_ASSERT(p >= base_ &&
                       uintptr_t(p) + bytes <=
                           uintptr_t(base_) + MaxCodeBytesPerProcess);
  }

  bool containsAddress(const void* p) const {
    return p >= base_ &&
           uintptr_t(p) < uintptr_t(base_) + MaxCodeBytesPerProcess;
  }
```

Each compares `p` and `base_` as pointers. C++ orders two pointers only when they point into the
same object (`[expr.rel]` 7.6.9/4–5, in N4860, C++20):

> (4.3) Otherwise, neither pointer is required to compare greater than the other.
>
> 5 If two operands p and q compare equal, [...]. If two operands p and q compare greater, [...].
> Otherwise, the result of each of the operators is unspecified.

So whenever `p` is outside the region, which is what these methods are for, `p >= base_` is
unspecified: it may be true or false. It isn't undefined behaviour, but the standard gives the
result no value. The other half of each condition already converts both pointers to `uintptr_t`,
which is implementation-defined (`[expr.reinterpret.cast]` 7.6.1.9/4) and gives the addresses.

- `containsAddress`: x64's `Assembler::addPendingJump` (`js/src/jit/x64/Assembler-x64.cpp`) calls
  `AddressIsInExecutableMemory(target)` for every jump or call to a fixed address
  (`call(ImmPtr)`, so every `callWithABI` with a fixed C++ function), to decide whether it's a
  near jump (`codeJumps_`) or an extended one (`extendedJumps_`), which `Assembler::finish` gives
  an entry of the extended jump table. For a C++ function, `p` is the function's address, which
  isn't an object, and `base_` the region's start: the comparison is unspecified on every call
  into C++ that x64 emits, and with it the code's size and bytes.
- `assertValidAddress`: `ReprotectRegion` and `ProcessExecutableMemory::deallocate` call it on
  pointers that should be in the region, in a `MOZ_RELEASE_ASSERT`. For one that is, the
  pointers are at least arguably related; for one that isn't, the case the assert exists to
  catch, the comparison is unspecified, and the assert can pass.

## What it affects

Every build that runs the JIT: `containsAddress` on x64, on every call from JIT code into C++;
`assertValidAddress` on every platform, when JIT code is made executable or freed.

It isn't observable in practice: Clang compiles `p >= base_` as an unsigned comparison of the
addresses, which is what the fix writes. Nothing is known to misbehave. It matters to tools that
give C++ its standard meaning: the Phoenix effort's Lean translation of x64's assembler can't
give `addPendingJump`'s choice a value while the comparison is there, so it can't say what code
a stub that calls into C++ is.

## Reproducing it

With a release shell without Phoenix, and the script of report 0004, which attaches the baseline
IC stub for `~` of a double, which calls `JS::ToInt32`:

```
# mozconfig-js-x64-release
ac_add_options --enable-project=js
ac_add_options --disable-debug
ac_add_options --enable-optimize
ac_add_options --as-milestone=release
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-x64-release
```

```js
// phoenix-tools/repro/bitnot-double.js
function bitNot(x) {
  return ~x;
}
let n = 0;
for (let i = 0; i < 100; i++) {
  n += bitNot(i + 0.5);
}
print(n, bitNot(1e20));
```

A gdb script, `phoenix-tools/repro/containsaddress.gdb`, stops in `addPendingJump` at the call
of `AddressIsInExecutableMemory` (`containsAddress` is inlined into it) when the target is
`JS::ToInt32`, and prints the two pointers the comparison is given:

```
set pagination off
set breakpoint pending on
break Assembler-x64.cpp:134 if target.value == (void*)&JS::ToInt32
commands
  silent
  printf "p     = %p\n", target.value
  printf "base_ = %p\n", 'ProcessExecutableMemory.cpp'::execMemory.base_
  info symbol target.value
  info symbol 'ProcessExecutableMemory.cpp'::execMemory.base_
  continue
end
run
```

```
gdb -q -batch -x containsaddress.gdb --args $OBJDIR/dist/bin/js --no-ion --baseline-eager bitnot-double.js
```

**Without the fix** (observed): `p` is `JS::ToInt32`, in the shell's `.text`, and `base_` the
JIT's region, in memory no object file maps:

```
p     = 0x555556ee11b0
base_ = 0x35df025dc000
JS::ToInt32(double) in section .text of …/obj-js-x64-release/dist/bin/js
No symbol matches 'ProcessExecutableMemory.cpp'::execMemory.base_.
-5050 -1661992961
```

The call becomes an extended jump, as intended: the stub's code ends with a table entry
(`ff 25 02 00 00 00`, `jmp *2(%rip)`; `0f 0b`, `ud2`; then `JS::ToInt32`'s address). **With the
fix** the script prints the same, `-5050 -1661992961`.

## The fix

```diff
   void assertValidAddress(void* p, size_t bytes) const {
-    MOZ_RELEASE_ASSERT(p >= base_ &&
+    MOZ_RELEASE_ASSERT(uintptr_t(p) >= uintptr_t(base_) &&
                        uintptr_t(p) + bytes <=
                            uintptr_t(base_) + MaxCodeBytesPerProcess);
   }

   bool containsAddress(const void* p) const {
-    return p >= base_ &&
+    return uintptr_t(p) >= uintptr_t(base_) &&
            uintptr_t(p) < uintptr_t(base_) + MaxCodeBytesPerProcess;
   }
```

Both halves of each condition now compare addresses, as the second halves already did: the
conversions are implementation-defined, and comparing the integers is defined (and so is the
second halves' addition, of unsigned integers).

**The generated code doesn't change.** In a release build (the mozconfig above, Mozilla's clang
22.1.8), the functions the comparisons are compiled into (`AddressIsInExecutableMemory`,
`Assembler::addPendingJump`, `ReprotectRegion`, `ProcessExecutableMemory::allocate` and
`deallocate`, `DeallocateExecutableMemory`) are the same size with and without the fix, and the
same instructions: `AddressIsInExecutableMemory` is, in both, a load of `base_`, a `cmp` and a
`setae`, an unsigned comparison of the addresses. The only differences are 11 `lea`s of addresses
in `.rodata` (the release asserts' messages), which the two builds lay out differently.

## How it was verified

On 2026-11-18: without the fix, on `phoenix` at `c291b1745c1e` (the pin `f35f2f1f3ae8` plus fixes
0001 to 0004), in `../firefox-phoenix`; with it, on `phoenix-dev` built without Phoenix (the same
plus this fix). Both with the release mozconfig above; the gdb run on the build without the fix.
