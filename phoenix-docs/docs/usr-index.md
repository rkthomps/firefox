# The USR index: the reference's C++ entities, for `cachet-lean`'s provenance checker

`cachet-lean` annotates each declaration that translates SpiderMonkey's C++ with the C++ entity's
name and its `file:line` (`@[translates …]`, `@[follows …]`). Its checker is to resolve each
annotation against an index of the reference's entities, keyed by clang's USR (Unified Symbol
Resolution), and report the annotations whose entity has moved or doesn't exist. The annotations
and the checker are `cachet-lean`'s, designed in its `docs/arch/provenance.md`. This file is the
index: `phoenix-tools/usr-index.py`, built on 2026-11-04, for x64 only (ARM: see
[TODO.md](../TODO.md)).

## Running it

```
phoenix-tools/usr-index.py            # x64: ../firefox-phoenix/obj-js-x64 → ../phoenix-index/x64.json
```

It needs:

1. **The reference checkout, unedited.** It indexes `../firefox-phoenix` (see "The reference
   checkout" in [branches.md](branches.md)), and refuses one with uncommitted changes. The index
   records the commit.
2. **The objdir's compile database.** In `../firefox-phoenix`:
   `MOZCONFIG=$PWD/mozconfig-js-x64 ./mach build-backend -b CompileDB`, which writes
   `obj-js-x64/compile_commands.json` in about a second. The objdir must have its generated
   headers, which a build makes (or, without compiling, `./mach build pre-export export`). Run
   CompileDB again when `phoenix` changes.

On 2026-11-04, at `phoenix` `a80a8d195688`: 689 translation units, 168,101 entities in 1,864
files, 66 s with 48 jobs, at most 340 MB of memory, a 54 MB file. The 33 x64 annotations in
`cachet-lean` then each resolved to one entity, at the line they give, except an example in
`Provenance.lean`'s docstring. It names `js::jit::WarpCacheIRTranspiler`, which is declared outside
any namespace.

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
- **A record per declaration,** deduplicated across translation units by USR, file, line, kind
  and whether it's a definition.

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
    specialization with its arguments (`geo::twice<int>`, `geo::Box<int>`). An argument that
    isn't a type or an integer is written `?`.
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
- `isDefinition`: whether this declaration is its definition.
- `macro`, only for an entity a macro's expansion declares: the macro's `name`, and the `file` and
  `line` of its definition, which can be in the objdir, like `CacheIRWriter`'s emitters.

## Limits

- **x64 only.** ARM's index needs a configuration whose catches are in [TODO.md](../TODO.md).
- **A debug build.** `obj-js-x64` is configured with `--enable-debug`, so the declarations under
  `#ifdef DEBUG` are indexed, and those under `#ifndef DEBUG` aren't. Lines don't depend on it.
