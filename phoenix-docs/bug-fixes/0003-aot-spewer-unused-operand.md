# 0003: the AOT IC spewer crashes on an operand no instruction uses

- **Where:** SpiderMonkey, `js/src/jit/CacheIRWriter.h`, `CacheIRWriter::operandLastUsed`, as
  called by `SpewCacheIROpsAsAOT` in `js/src/jit/CacheIRSpewer.cpp`.
- **Since:** bug 1929125 (December 2024), which added both, with ahead-of-time ICs.
- **Upstream:** still on `main` at `0b6146da78e5` (2026-10-30). Not filed.
- **Fixed on `phoenix`** by the commit `[BUG FIX] Handle operands no instruction uses in the AOT
  IC spewer`.

## What's wrong

`SpewCacheIROpsAsAOT` serializes a `CacheIRWriter` as one line of text for the ahead-of-time IC
corpus (`js/src/ics/IC-*`). Part of that line is, for each operand ID, the last instruction that
uses it:

```cpp
  for (uint32_t i = 0; i < writer.numOperandIds(); i++) {
    out.printf("LASTUSED(%u) ", writer.operandLastUsed(i));
  }
```

`CacheIRWriter::operandLastUsed` indexes `operandLastUsed_` without checking:

```cpp
  uint32_t operandLastUsed(uint32_t operandId) const {
    return operandLastUsed_[operandId];
  }
```

But `operandLastUsed_` doesn't have an entry for every operand ID. It grows only in
`writeOperandId`, when an instruction mentions an operand, and the entries it skips over are 0.
`numOperandIds()` also counts the input operands (`setInputOperandId`), whether or not an
instruction mentions them. Every other operand is mentioned by the instruction that creates it:
the generated emitters call `newOperandId()` and then `writeOperandId(result)`. So when the
highest-numbered operands are input operands no instruction mentions, the loop reads past the end
of `operandLastUsed_`. `mozilla::Vector`'s bounds check, which is on in release builds too,
crashes the process.

A stub whose instructions mention no operand at all is enough. The self-hosted intrinsic
`NewArrayIterator`, which a `for-of` over an array calls, makes one:
`InlinableNativeIRGenerator::tryAttachNewArrayIterator` initializes the `Call` IC's input operand
(`argc`), skips the callee guard as intrinsics do, and emits only `NewArrayIteratorResult`.

`operandIsDead`, the other reader of `operandLastUsed_`, already handles an operand without an
entry: such an operand is never dead.

## What it affects

Builds with `--enable-aot-ics`, which requires `--enable-portable-baseline-interp`, when new ICs
are dumped to extend the corpus (`--enforce-aot-ics`, with `AOT_ICS_KEEP_GOING=1` to dump them
all). PBL's defaults don't hide it: in its hybrid IC mode, calls still use ICs. Without
`AOT_ICS_KEEP_GOING`, the shell quits after dumping the first new IC, so it crashes only when
this stub is the first. The crash leaves an empty `IC-*` file behind. Anything else that
serializes a writer with `SpewCacheIROpsAsAOT` crashes the same way (Phoenix's recorder did, in a
JIT build, as soon as it recorded every kind of IC).

## Reproducing it

Build a PBL shell with AOT ICs:

```
# mozconfig-js-pbl
ac_add_options --enable-project=js
ac_add_options --enable-debug
ac_add_options --enable-portable-baseline-interp
ac_add_options --enable-aot-ics
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-js-pbl
```

The script, `phoenix-tools/repro/array-iterator.js`:

```js
let n = 0;
for (let i = 0; i < 20; i++) {
  for (const x of [1, 2, 3]) {
    n += x;
  }
}
print(n);
```

Run it in an empty directory, since each new IC is dumped to an `IC-<random>` file there:

```
mkdir ics && cd ics
AOT_ICS_KEEP_GOING=1 $OBJDIR/dist/bin/js --portable-baseline --portable-baseline-eager \
  --aot-ics --enforce-aot-ics array-iterator.js
grep -h 'NewArrayIteratorResult' IC-*
```

**Without the fix** (observed): four ICs are dumped, then the shell dies with exit status 139
while dumping the fifth, whose `IC-*` file is left empty. The script doesn't print its result.

```
Hit MOZ_CRASH(ElementAt(aIndex = 0, aLength = 0)) at mfbt/Assertions.cpp:51
```

**With the fix** (observed): the script prints `120`, all 23 new ICs are dumped, and the `Call`
IC that calls `NewArrayIterator` is:

```
Call, 1, 1, 1, JSVAL_TYPE_UNKNOWN, 8, STUBFIELD(JSObject) , LASTUSED(0) , OP(NewArrayIteratorResult) OFFSET(0)
```

With that file in `js/src/ics/`, the build succeeds, and running the script again doesn't dump
that stub: it's found in the corpus.

## The fix

```diff
-  uint32_t operandLastUsed(uint32_t operandId) const {
-    return operandLastUsed_[operandId];
+  uint32_t operandLastUsed(uint32_t id) const {
+    return id < operandLastUsed_.length() ? operandLastUsed_[id] : 0;
   }
```

An operand without an entry is now spewed as `LASTUSED(0)`. That's the value `operandLastUsed_`
already holds for an input operand no instruction mentions, whenever a higher-numbered operand is
mentioned, since the entries `resize` adds are 0. The script above dumps such a `Call` IC, with or
without the fix: its instructions mention only operand 1, and `argc`, operand 0, is
`LASTUSED(0)`:

```
Call, 1, 2, 3, JSVAL_TYPE_UNKNOWN, 0, , LASTUSED(0) LASTUSED(2) , OP(LoadArgumentFixedSlot) ID(1) BYTE(0) OP(GuardToObject) ID(1) OP(LoadObjectResult) ID(1)
```

So an operand no instruction mentions is now spewed the same, whatever its number. The parameter
is renamed so that the line fits in 80 columns.

The corpus loader (`CacheIRWriter`'s constructor in `CacheIRAOT.cpp`) gives the rebuilt writer an
entry for every operand ID, so there such an operand is dead after instruction 0, where the
original writer has it never dead. The compiler doesn't ask about it either way:
`CacheRegisterAllocator::freeDeadOperandLocations` skips the input operands, and
`isDeadAfterInstruction` is only asked about the operands of the current instruction. So the
rebuilt writer compiles to the same code.

Nothing else calls `operandLastUsed`. `operandLastUsed_`, `writeOperandId` and `operandIsDead`
are unchanged, so the code the CacheIR compilers generate doesn't change.

## How it was verified

On 2026-11-04, with `mozconfig-js-pbl`: the run above without the fix on `phoenix` at
`4b2bb24ae562` (the pin `f35f2f1f3ae8` plus fixes 0001 and 0002), and with it on `phoenix-dev`
built without Phoenix (the same plus this fix), then the corpus build with the dumped file.
Phoenix's recorder (`PHOENIX_RECORD`, in `obj-js-x64`) crashed the same way without the fix when
recording every kind of IC.
