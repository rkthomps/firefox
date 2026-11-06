# The USR index: the reference's C++ entities, for `cachet-lean`'s provenance checker

`cachet-lean` annotates each declaration that translates SpiderMonkey's C++ with the C++ entity's
name and its `file:line` (`@[translates …]`, `@[follows …]`). Its checker is to resolve each
annotation against an index of the reference's entities, keyed by clang's USR (Unified Symbol
Resolution), and report the annotations whose entity has moved or doesn't exist. The annotations
and the checker are `cachet-lean`'s, designed in its `docs/arch/provenance.md`. This file is the
index: `phoenix-tools/usr-index.py`, built on 2026-11-04 for x64, and on 2026-11-06 for 32-bit
ARM.

## Running it

```
phoenix-tools/usr-index.py                                # x64: obj-js-x64 → ../phoenix-index/x64.json
phoenix-tools/usr-index.py --objdir obj-js-arm --build arm   # ARM: obj-js-arm → ../phoenix-index/arm.json
```

It needs:

1. **The reference checkout, unedited.** It indexes `../firefox-phoenix` (see "The reference
   checkout" in [branches.md](branches.md)), and refuses one with uncommitted changes. The index
   records the commit.
2. **The objdir's compile database.** In `../firefox-phoenix`, with `MOZCONFIG` set to the
   build's mozconfig (`mozconfig-js-x64`, or ARM's `mozconfig-js-arm`, in
   [spidermonkey.md](spidermonkey.md)): `./mach build-backend -b CompileDB`, which writes
   `compile_commands.json` in the objdir in about a second. The objdir must have its generated
   files, which a build makes, or, without compiling, `./mach build pre-export export
   pre-compile` (a few seconds): `export` makes the headers, and `pre-compile` the rest, such as
   `gc/StatsPhasesGenerated.inc`. Run both again when `phoenix` changes.

ARM's objdir is only configured, never built: configure, the generated files, then CompileDB.
It needs ARM's sysroot and Rust target ([backends.md](backends.md)).

On 2026-11-04, at `phoenix` `a80a8d195688`, x64: 689 translation units, 168,101 entities in 1,864
files, 66 s with 48 jobs, at most 340 MB of memory, a 54 MB file. The 33 x64 annotations in
`cachet-lean` then each resolved to one entity, at the line they give, except an example in
`Provenance.lean`'s docstring. It names `js::jit::WarpCacheIRTranspiler`, which is declared outside
any namespace.

On 2026-11-06, at the same commit, ARM: 647 translation units, 159,005 entities in 1,749 files
(3,831 in `js/src/jit/arm/`, none in `x64/`), 59 s, a 51 MB file. `cachet-lean`'s
`lake exe check-translations`, against both indexes, then matched 322 of its 323 annotations
uniquely. The other, `JS::ToSignedInteger<int32_t>` on ARM, was indexed as
`JS::ToSignedInteger<int>`, since functions' template arguments were canonical types then, and
methods' were left out. With them as written, the same day, all 323 matched uniquely. That
renamed 346 records of x64's index and 344 of ARM's, in their arguments only, and 119 and 121
conversion functions; no record was added or lost. Then, with `= default` and `= delete`
counted as definitions: 167,824 entities on x64 and 158,731 on ARM, 277 and 274 fewer, each a
defaulted function's second record; 1,076 and 1,433 records became definitions; all 323
annotations still matched uniquely.

Options:

- `--checkout DIR`, `--objdir NAME` (from the checkout), `--build NAME` (the index's `build`;
  `x64` by default), `-o FILE` (default `../phoenix-index/BUILD.json`);
- `--path PREFIX`, repeatable: index the entities declared under these paths, from the checkout's
  root (default `js/` and `mfbt/`);
- `--jobs N` (default: every CPU), `--only SUBSTRING` (parse only the inputs whose path contains
  it, for trying something out);
- `--keep-going`: write the index even if a translation unit has errors (by default, any error
  fails the run, since the index would miss that unit's entities).

Its tests run it on a hand-made fixture (`phoenix-tools/tests/usr-index/`):
`python3 phoenix-tools/tests/test-usr-index.py`.

## Dependencies

- **libclang 22.1.8**, `~/.mozbuild/clang/lib/libclang.so`, from `./mach bootstrap`: the clang that
  builds the tree, so it parses the code as the build does. Nothing to install.
- **libclang's Python bindings**, vendored in `phoenix-tools/third_party/clang/`: `__init__.py` and
  `cindex.py`, pure Python, and clang's `LICENSE.TXT` (Apache 2.0 with LLVM exceptions), from
  LLVM's tag `llvmorg-22.1.8`
  (`https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-22.1.8/clang/bindings/python/clang/`,
  and `clang/LICENSE.TXT`). Mozilla's toolchain has libclang but not the bindings, PyPI's `clang`
  package stops at 21.1.7, and this machine's Python has no `pip`.
- The other tools' dependencies, which it shares, are in the README's "Dependencies".

The script refuses a libclang whose version, from its file name (`libclang.so.22.1.8`), isn't the
bindings'. When `./mach bootstrap` moves clang to another version, replace the three vendored
files from LLVM's matching tag, and update `BINDINGS_VERSION` in `usr-index.py`.

## How it works

- **The translation units are the build's.** It parses each distinct input of the compile
  database once, with its flags (minus `-c`, `-o` and the input), plus clang's `-resource-dir`,
  which libclang doesn't find on its own. The CompileDB backend lists each member of a unified
  bundle under its own name but with the bundle's command, so a bundle is parsed once, as the build
  compiles it, and its members' entities get their own paths. A flag for one file alone comes after
  its input.
- **Function bodies are parsed.** Skipping them would be twice as fast, but libclang then reports
  definitions as declarations and cuts their extents.
- **What's indexed:** declarations at namespace and class scope whose file is under the `--path`
  prefixes, after resolving symlinks (the objdir's `dist/include` links to source headers), and
  macro definitions. Not indexed: declarations inside function bodies, parameters, template
  parameters, `using` and `friend` declarations.
- **A specialization's arguments are read from its declaration as clang prints it.** libclang's
  template-argument API gives only canonical types (`int` for `int32_t`), and none for a method's.
  clang's `DeclPrinter` (`VisitFunctionDecl` in `clang/lib/AST/DeclPrinter.cpp`) writes a
  specialization's name, then its arguments as written, or the deduced ones where none are, then
  `(` and the parameters. So the indexer takes the `<…>` that follows the name and comes before a
  `(`, and fails the translation unit, like a parse error, unless there's exactly one. A
  specialization is a function whose `specialized_template` is a function template. On
  2026-11-06, x64's 369 and ARM's 367 function specializations each had exactly one. Reading the
  source's tokens instead would miss 200 of them: 124 write no arguments, and 76 come from macros,
  where the tokens are the macro's parameters. A conversion function's type is its result type's
  spelling, which keeps the qualifiers as written; its own spelling has clang's canonical one.
- **A definition is one by clang's rule** (`FunctionDecl::isThisDeclarationADefinition`): a
  function declared `= default` or `= delete` is one, since those are function bodies
  ([dcl.fct.def.general]). libclang's `is_definition` counts only a body, which clang makes for a
  defaulted function only in translation units that use it, so translation units would disagree,
  and the function would get two records. libclang tells whether a method is defaulted or
  deleted. It can't for a free function, which can't be defaulted (but a friend comparison, not
  indexed); a deleted one is printed ending in ` = delete`. On 2026-11-06, x64's build had 850
  defaulted methods, 767 deleted ones, and 25 deleted free functions, 22 of them templates, which
  libclang's availability query, the only other sign, doesn't cover.
- **A record per declaration,** deduplicated across translation units by USR, file, line, kind
  and whether it's a definition. A macro whose expansion declares an entity and then defines it
  gives two records at one line, one of each `isDefinition`: on 2026-11-06, 192 on x64, in
  `js/src/vtune/` (132, built only on x86), `js/src/gdb/` (52) and `js/src/shell/` (8), and 60 on
  ARM.

## The format

One JSON file per build, as agreed in `cachet-lean`'s `provenance.md`, with two additions (`union`,
`macro`):

```json
{"build": "x64", "commit": "a80a8d195688", "entities": [
{"usr": "c:Interpreter-inl.h@N@js@F@BitNotOperation#*$@S@JSContext#$@N@JS@S@MutableHandle>#$@N@JS@S@Value#S2_#", "name": "js::BitNotOperation", "signature": "js::BitNotOperation(JSContext *, MutableHandleValue, MutableHandleValue)", "kind": "function", "file": "js/src/vm/Interpreter-inl.h", "line": 776, "endLine": 789, "isDefinition": true},
{"usr": "c:@N@js@N@jit@S@CacheIRWriter@F@int32NotResult#$@N@js@N@jit@S@Int32OperandId#", "name": "js::jit::CacheIRWriter::int32NotResult", "signature": "js::jit::CacheIRWriter::int32NotResult(Int32OperandId)", "kind": "method", "file": "js/src/jit/CacheIRWriter.h", "line": 744, "endLine": 744, "isDefinition": true, "macro": {"name": "CACHE_IR_WRITER_GENERATED", "file": "obj-js-x64/js/src/jit/CacheIROpsGenerated.h", "line": 479}}
]}
```

One entity per line, sorted by file and line. Each entity has:

- `usr`: clang's USR. A `static` function's starts with its file's name.
- `name`: its qualified name, as clang writes it.
  - Namespaces and classes qualify it; an anonymous namespace is `(anonymous namespace)`.
  - An unscoped enum doesn't qualify its enumerators (`geo::Red`), and a scoped one does
    (`geo::Kind::Circle`).
  - A template is named without its parameters (`geo::twice`, `geo::Box`), and an explicit
    specialization with its arguments as written at its line, typedefs included
    (`geo::twice<int>`, `geo::Box<int>`, `geo::half<Count>`, `geo::Holder::is<Shape>`). Where a
    function's declaration writes none, its arguments are the deduced ones, which are canonical
    types (`geo::half<double>`), so they can differ by build (see "Limits").
  - A conversion function is named by the type it converts to, as written
    (`JS::GCVector::operator mozilla::Span<T>`).
- `signature`: for a function, its name and its parameters as written in the declaration
  (libclang's spelling), and ` const` after a const method's, which libclang leaves out but tells
  overloads apart. `null` for anything else.
- `kind`: `function`, `method` (also destructors, conversion functions and member function
  templates), `constructor`, `class` (also class templates and partial specializations), `struct`,
  `union`, `enum`, `enumerator`, `variable`, `field`, `typedef` (also `using` aliases) or `macro`.
- `file`, `line`: where its name is, from the root of the checkout. For an entity a macro declares,
  where the macro is expanded: always a file of the checkout, even when the macro is generated in
  the objdir.
- `endLine`: the last line of its extent (the same as `line` when the extent ends elsewhere).
- `isDefinition`: whether this declaration is its definition. A function declared `= default` or
  `= delete` is one.
- `macro`, only for an entity a macro's expansion declares: the macro's `name`, and the `file` and
  `line` of its definition, which can be in the objdir, like `CacheIRWriter`'s emitters.

## Limits

- **Two builds:** x64, and 32-bit ARM, cross-compiled for real ARM, not the simulator
  ([backends.md](backends.md)). A simulator build compiles the code under `#ifdef JS_SIMULATOR`
  in place of ARM's: there, `BaselineCacheIRCompiler::emitCallNativeFunction` would be at the
  simulator's definition, and `__arm__`'s code, like `ToSignedInteger<int32_t>`, wouldn't be
  indexed. A build's `size_t` is in its USRs: `unsigned long` (`l`) on x64, `unsigned int`
  (`i`) on ARM.
- **Debug builds.** Both objdirs are configured with `--enable-debug`, so the declarations under
  `#ifdef DEBUG` are indexed, and those under `#ifndef DEBUG` aren't. Lines don't depend on it.
- **Names that differ by build.** Where nothing is written to keep a typedef, a name has
  canonical types, which can differ between x64 and ARM: deduced arguments, such as
  `AtomicOperations::compareExchangeSeqCst<long>` on x64 and `<long long>` on ARM, from a macro
  that writes no `<T>` (4 records), and a class template's implicit instantiation in a member's
  name (6 records, a flaw below). On 2026-11-06, those were the 10 names with arguments that
  differ between the two indexes at the same line.
- **A flaw, to fix** (see [TODO.md](../TODO.md)): a member of a class template's implicit
  instantiation is named with the instantiation's canonical arguments.
