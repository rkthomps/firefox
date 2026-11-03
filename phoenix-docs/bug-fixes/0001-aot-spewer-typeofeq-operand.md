# 0001: the AOT IC spewer writes a `TypeofEqOperand` as the argument's name

- **Where:** SpiderMonkey, `js/src/jit/CacheIRSpewer.cpp`,
  `CacheIROpsAotSpewer::spewTypeofEqOperandImm`.
- **Since:** bug 1929125 ("Add ahead-of-time ICs", December 2024), which added the AOT spewer.
- **Upstream:** still on `main` at `0b6146da78e5` (2026-10-30). Not filed.
- **Fixed on `phoenix-dev`** by the commit `[BUG FIX] Print TypeofEqOperand arguments in the
  AOT IC spewer`.

## What's wrong

`SpewCacheIROpsAsAOT` serializes a `CacheIRWriter` as one line of text for the ahead-of-time IC
corpus (`js/src/ics/IC-*`). For a `TypeofEqOperand` argument it calls:

```cpp
void spewTypeofEqOperandImm(const char* name, TypeofEqOperand operand) {
  (void)name;
  out_.printf(name, "TYPEOFEQOPERAND(%u)", operand.rawValue());
}
```

The first argument of `printf` is the format string, so this prints the argument's name from
`CacheIROps.yaml`, `operand`, and ignores the rest. The one op with such an argument,
`LoadTypeOfEqObjectResult`, comes out as `OP(LoadTypeOfEqObjectResult) ID(0) operand` instead of
`OP(LoadTypeOfEqObjectResult) ID(0) TYPEOFEQOPERAND(2)`. The operand (the `JSType` and whether
the comparison is `!==`) is lost, and the line isn't valid corpus input: added to `js/src/ics/`,
it breaks the build.

## What it affects

Builds with `--enable-aot-ics`, which requires `--enable-portable-baseline-interp`, when a new IC
is dumped to extend the corpus (`--enforce-aot-ics`, with `AOT_ICS_KEEP_GOING=1` to dump them
all). With PBL's defaults it's latent: when hybrid ICs are on (`PBL_HYBRID_ICS_DEFAULT` is `true`
in `vm/PortableBaselineInterpret.cpp`), PBL computes `JSOp::TypeofEq` inline and never attaches a
`TypeOfEq` stub. It shows as soon as hybrid ICs are off, and in anything else that serializes a
writer with `SpewCacheIROpsAsAOT` (Phoenix's recorder does, in a JIT build).

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

To make PBL attach `TypeOfEq` stubs, turn hybrid ICs off for the reproduction (a local edit,
not part of the fix): in `js/src/vm/PortableBaselineInterpret.cpp`,
`#define PBL_HYBRID_ICS_DEFAULT false`. Then `MOZCONFIG=mozconfig-js-pbl ./mach build`.

The script, `phoenix-tools/repro/typeofeq.js`:

```js
function isFunction(x) {
  return typeof x === "function";
}
let n = 0;
for (let i = 0; i < 100; i++) {
  n += isFunction({}) ? 1 : 0;
}
print(n);
```

Run it in an empty directory, since each new IC is dumped to an `IC-<random>` file there:

```
mkdir ics && cd ics
AOT_ICS_KEEP_GOING=1 $OBJDIR/dist/bin/js --portable-baseline --portable-baseline-eager \
  --aot-ics --enforce-aot-ics typeofeq.js
grep -h TypeOfEq IC-*
```

**Without the fix** (observed):

```
TypeOfEq, 1, 1, 2, JSVAL_TYPE_OBJECT, 0, , LASTUSED(1) , OP(GuardToObject) ID(0) OP(LoadTypeOfEqObjectResult) ID(0) operand
```

Copying that file into `js/src/ics/` and rebuilding fails:

```
js/src/jit/CacheIRAOT.cpp:76:1: error: use of undeclared identifier 'operand'
```

**With the fix** (observed):

```
TypeOfEq, 1, 1, 2, JSVAL_TYPE_OBJECT, 0, , LASTUSED(1) , OP(GuardToObject) ID(0) OP(LoadTypeOfEqObjectResult) ID(0) TYPEOFEQOPERAND(2)
```

With that file in `js/src/ics/`, the build succeeds, and running the script again dumps no
`TypeOfEq` IC: the stub is found in the corpus.

## The fix

```diff
   void spewTypeofEqOperandImm(const char* name, TypeofEqOperand operand) {
     (void)name;
-    out_.printf(name, "TYPEOFEQOPERAND(%u)", operand.rawValue());
+    out_.printf("TYPEOFEQOPERAND(%u)", operand.rawValue());
   }
```

This is how every other `spew*Imm` method of `CacheIROpsAotSpewer` prints, and `(void)name;`
already marks `name` as unused. `TYPEOFEQOPERAND(value)` is what the corpus loader expects:
`CacheIRAOT.cpp` defines it as `value,`, the raw byte that `TypeofEqOperand::fromRawValue` reads
back.

## How it was verified

On 2026-11-03, on `phoenix-dev` (the pin `f35f2f1f3ae8` plus this fix), with
`mozconfig-js-pbl` and hybrid ICs off: the run above without the fix and with it, and the corpus
build with each dumped file. Phoenix's recorder (`PHOENIX_RECORD`, in `obj-js-x64`) gave the
same two lines before and after.
