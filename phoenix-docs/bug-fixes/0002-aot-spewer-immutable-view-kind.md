# 0002: the AOT IC spewer crashes on `ArrayBufferViewKind::Immutable`

- **Where:** SpiderMonkey, `js/src/jit/CacheIRSpewer.cpp`, `ArrayBufferViewKindName`.
- **Since:** immutable ArrayBuffers (bug 1966355, May 2025) added
  `ArrayBufferViewKind::Immutable` to `jit/CacheIR.h`. The AOT spewer, from bug 1929125
  (December 2024), wasn't updated.
- **Upstream:** still on `main` at `0b6146da78e5` (2026-10-30). Not filed.
- **Fixed on `phoenix-dev`** by the commit `[BUG FIX] Handle ArrayBufferViewKind::Immutable in
  the AOT IC spewer`.

## What's wrong

`SpewCacheIROpsAsAOT` serializes a `CacheIRWriter` as one line of text for the ahead-of-time IC
corpus (`js/src/ics/IC-*`). It names an `ArrayBufferViewKind` argument with:

```cpp
static const char* ArrayBufferViewKindName(ArrayBufferViewKind kind) {
  switch (kind) {
    case ArrayBufferViewKind::FixedLength:
      return "FixedLength";
    case ArrayBufferViewKind::Resizable:
      return "Resizable";
  }
  MOZ_CRASH("Unknown ArrayBufferViewKind");
}
```

`ArrayBufferViewKind` is `FixedLength`, `Immutable` or `Resizable`. For `Immutable`, the switch
falls through to `MOZ_CRASH`. Any stub that accesses an immutable typed array or `DataView`
crashes the process when it's serialized. The ops with such an argument are
`LoadTypedArrayElementResult`, `LoadTypedArrayElementExistsResult`, `StoreTypedArrayElement`,
`LoadDataViewValueResult`, `StoreDataViewValueResult` and the nine `Atomics*Result` ops.

The compiler sees it: building the AOT spewer gives
`CacheIRSpewer.cpp:520:11: warning: enumeration value 'Immutable' not handled in switch
[-Wswitch]`.

## What it affects

Builds with `--enable-aot-ics`, which requires `--enable-portable-baseline-interp`, when a new IC
is dumped to extend the corpus (`--enforce-aot-ics`, with `AOT_ICS_KEEP_GOING=1` to dump them
all), for code that uses immutable ArrayBuffers. Those are Nightly-only
(`ArrayBuffer.prototype.transferToImmutable`) and behind the pref
`javascript.options.experimental.arraybuffer_immutable`, which the shell sets with
`--setpref=experimental.arraybuffer_immutable=true`. The crash also leaves an empty `IC-*` file
behind. Anything else that serializes a writer with `SpewCacheIROpsAsAOT` crashes the same way
(Phoenix's recorder did, in a JIT build).

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

The script, `phoenix-tools/repro/immutable-view.js`:

```js
const ta = new Int32Array(new ArrayBuffer(16).transferToImmutable());
let sum = 0;
for (let i = 0; i < 100; i++) {
  sum += ta[i & 3] + ta.length;
}
print(sum);
```

Run it in an empty directory, since each new IC is dumped to an `IC-<random>` file there:

```
mkdir ics && cd ics
AOT_ICS_KEEP_GOING=1 $OBJDIR/dist/bin/js --portable-baseline --portable-baseline-eager \
  --aot-ics --enforce-aot-ics --setpref=experimental.arraybuffer_immutable=true \
  immutable-view.js
grep -h 'ARRAYBUFFERVIEWKIND' IC-*
```

**Without the fix** (observed): the shell dies with exit status 139, and one `IC-*` file is
empty.

```
Hit MOZ_CRASH(Unknown ArrayBufferViewKind) at js/src/jit/CacheIRSpewer.cpp:526
```

**With the fix** (observed): the script prints `400`, and the `GetElem` stub is dumped:

```
GetElem, 2, 3, 5, JSVAL_TYPE_UNKNOWN, 8, STUBFIELD(WeakShape) , LASTUSED(4) LASTUSED(3) LASTUSED(4) , OP(GuardToObject) ID(0) OP(GuardShape) ID(0) OFFSET(0) OP(GuardToInt32) ID(1) OP(Int32ToIntPtr) ID(1) ID(2) OP(LoadTypedArrayElementResult) ID(0) ID(2) SCALARTYPE(Int32) BOOL(0) BOOL(0) ARRAYBUFFERVIEWKIND(Immutable)
```

With that file in `js/src/ics/`, the build succeeds, and running the script again doesn't dump
that stub: it's found in the corpus.

## The fix

```diff
   switch (kind) {
     case ArrayBufferViewKind::FixedLength:
       return "FixedLength";
+    case ArrayBufferViewKind::Immutable:
+      return "Immutable";
     case ArrayBufferViewKind::Resizable:
       return "Resizable";
   }
```

As the comment above these helpers says, each string must be the constant's own name: the corpus
loader (`CacheIRAOT.cpp`) defines `ARRAYBUFFERVIEWKIND(name)` as
`uint8_t(ArrayBufferViewKind::name)`.

## How it was verified

On 2026-11-03, on `phoenix-dev` (the pin `f35f2f1f3ae8` plus this fix), with
`mozconfig-js-pbl`: the run above without the fix and with it, and the corpus build with the
dumped file. Phoenix's recorder (`PHOENIX_RECORD`, in `obj-js-x64`) crashed the same way without
the fix, and recorded the stub with it.
