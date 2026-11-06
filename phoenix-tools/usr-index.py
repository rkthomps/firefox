#!/usr/bin/env python3
"""Index the C++ entities of a build of the reference checkout, by USR.

  usr-index.py [--checkout DIR] [--objdir NAME] [--build NAME] [-o FILE]
               [--path PREFIX]... [--jobs N] [--only SUBSTRING] [--keep-going]

It parses each translation unit the build compiles, with the build's flags: one per distinct
input of the objdir's compile_commands.json (`./mach build-backend -b CompileDB`), so a unified
bundle once, for all its members. It writes every entity declared in a file under the --path
prefixes (js/ and mfbt/ by default) as JSON, for cachet-lean's checker of its provenance
annotations. The format, and how to set it up, are in phoenix-docs/docs/usr-index.md.
"""

import argparse
import bisect
import collections
import json
import multiprocessing
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Iterable
from typing import Any, NoReturn, TypeGuard

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TOOLS, "third_party"))

from clang import cindex  # noqa: E402

# An entity of the index, as written to JSON (phoenix-docs/docs/usr-index.md).
Record = dict[str, Any]
# A translation unit to parse: the directory to parse it in, its input, and its flags.
Task = tuple[str, str, list[str]]
# A macro's expansion in a file: its start and end offsets, and the macro as a record's `macro`.
Expansion = tuple[int, int, dict[str, Any]]

# The version of the vendored bindings in third_party/clang. libclang must be the same.
BINDINGS_VERSION = "22.1.8"
DEFAULT_LIBCLANG = os.path.expanduser("~/.mozbuild/clang/lib/libclang.so")
DEFAULT_PATHS = ("js/", "mfbt/")

# Each worker's state, which init_worker sets.
CHECKOUT = ""
PATHS: tuple[str, ...] = ()
PATH_CACHE: dict[str, str | None] = {}
INDEX: cindex.Index

K = cindex.CursorKind

# The cursors whose children are indexed. Function bodies aren't.
SCOPES = {
    K.NAMESPACE,
    K.LINKAGE_SPEC,
    K.UNEXPOSED_DECL,
    K.CLASS_DECL,
    K.STRUCT_DECL,
    K.UNION_DECL,
    K.CLASS_TEMPLATE,
    K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
    K.ENUM_DECL,
}
CLASSES = {
    K.CLASS_DECL,
    K.STRUCT_DECL,
    K.UNION_DECL,
    K.CLASS_TEMPLATE,
    K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
}
FUNCTIONS = {
    K.FUNCTION_DECL,
    K.FUNCTION_TEMPLATE,
    K.CXX_METHOD,
    K.CONSTRUCTOR,
    K.DESTRUCTOR,
    K.CONVERSION_FUNCTION,
}
KINDS = {
    K.FUNCTION_DECL: "function",
    K.FUNCTION_TEMPLATE: "function",
    K.CXX_METHOD: "method",
    K.DESTRUCTOR: "method",
    K.CONVERSION_FUNCTION: "method",
    K.CONSTRUCTOR: "constructor",
    K.CLASS_DECL: "class",
    K.CLASS_TEMPLATE: "class",
    K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION: "class",
    K.STRUCT_DECL: "struct",
    K.UNION_DECL: "union",
    K.ENUM_DECL: "enum",
    K.ENUM_CONSTANT_DECL: "enumerator",
    K.VAR_DECL: "variable",
    K.FIELD_DECL: "field",
    K.TYPEDEF_DECL: "typedef",
    K.TYPE_ALIAS_DECL: "typedef",
    K.TYPE_ALIAS_TEMPLATE_DECL: "typedef",
    K.MACRO_DEFINITION: "macro",
}


def fail(message: str) -> NoReturn:
    sys.exit(f"usr-index.py: {message}")


def git(checkout: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", checkout, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        fail(f"git {' '.join(args)} in {checkout} failed:\n{result.stderr}")
    return result.stdout


# The parent: what to parse.


SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx")


def tasks_of(compile_commands: str) -> list[Task]:
    """One task per distinct (directory, input) the build compiles: (directory, input, flags).

    The CompileDB backend writes the flags, then the input (a unified member's command compiles
    its bundle), then any flags for that file alone."""
    with open(compile_commands) as f:
        entries: list[dict[str, Any]] = json.load(f)
    tasks: dict[tuple[str, str], Task] = {}
    resource_dirs: dict[str, str] = {}
    for entry in entries:
        argv: list[str] = entry.get("arguments") or shlex.split(entry["command"])
        inputs = [
            i
            for i, arg in enumerate(argv)
            if i > 0 and arg.endswith(SOURCE_SUFFIXES) and argv[i - 1] not in ("-o", "-include")
        ]
        if not inputs:
            continue
        source = argv[inputs[-1]]
        key = (entry["directory"], source)
        if key in tasks:
            continue
        if argv[0] not in resource_dirs:
            resource_dirs[argv[0]] = subprocess.run(
                [argv[0], "-print-resource-dir"], capture_output=True, text=True, check=True
            ).stdout.strip()
        flags: list[str] = []
        skip = False
        for i, arg in enumerate(argv[1:], start=1):
            if skip:
                skip = False
            elif arg == "-o":
                skip = True
            elif arg != "-c" and i != inputs[-1]:
                flags.append(arg)
        flags += ["-resource-dir", resource_dirs[argv[0]]]
        tasks[key] = (entry["directory"], source, flags)
    # Unified bundles are the largest: start them first.
    return sorted(tasks.values(), key=lambda t: "Unified_" not in t[1])


# The workers: parse one translation unit, and list its entities.


def init_worker(libclang: str, checkout: str, paths: list[str]) -> None:
    global CHECKOUT, PATHS, PATH_CACHE, INDEX
    if not cindex.Config.loaded:
        cindex.Config.set_library_file(libclang)
    CHECKOUT = checkout
    PATHS = tuple(paths)
    PATH_CACHE = {}
    INDEX = cindex.Index.create()


def checkout_path(file: cindex.File | None) -> str | None:
    """|file|'s path from the root of the checkout, through symlinks such as the objdir's
    dist/include, or None if it's outside the checkout."""
    if file is None:
        return None
    name = file.name
    if name not in PATH_CACHE:
        rel = os.path.relpath(os.path.realpath(name), CHECKOUT)
        PATH_CACHE[name] = None if rel == ".." or rel.startswith("../") else rel
    return PATH_CACHE[name]


def indexed(path: str | None) -> TypeGuard[str]:
    return path is not None and path.startswith(PATHS)


def class_name(cursor: cindex.Cursor) -> str:
    """A class's name in qualified names: with its arguments if it's a specialization
    (`Foo<int>`), without its parameters if it's a template."""
    if cursor.is_anonymous():
        return f"(anonymous {KINDS[cursor.kind]})"
    if cursor.kind == K.CLASS_TEMPLATE:
        return cursor.spelling
    return cursor.displayname


def scope_of(cursor: cindex.Cursor) -> str:
    """The qualified name of the scope |cursor| is a member of, e.g. `js::jit::CacheIRWriter`.
    Like clang's qualified names, an unscoped enum isn't a scope of its enumerators."""
    parts: list[str] = []
    parent = cursor.semantic_parent
    while parent is not None and parent.kind != K.TRANSLATION_UNIT:
        if parent.kind == K.NAMESPACE:
            parts.append(parent.spelling or "(anonymous namespace)")
        elif parent.kind in CLASSES:
            parts.append(class_name(parent))
        elif parent.kind == K.ENUM_DECL and parent.is_scoped_enum():
            parts.append(parent.spelling)
        parent = parent.semantic_parent
    return "::".join(reversed(parts))


def without_parameters(displayname: str) -> str:
    """`foo<int>(int, char)` → `foo<int>`, `operator()(int)` → `operator()`."""
    if not displayname.endswith(")"):
        return displayname
    depth = 0
    for i in range(len(displayname) - 1, -1, -1):
        if displayname[i] == ")":
            depth += 1
        elif displayname[i] == "(":
            depth -= 1
            if depth == 0:
                return displayname[:i]
    return displayname


def qualify(scope: str, name: str) -> str:
    return f"{scope}::{name}" if scope else name


class UnnamedSpecialization(Exception):
    """A function template specialization whose arguments aren't where clang prints them."""


def closing_angle(text: str, start: int) -> int | None:
    """The index of the `>` that closes the `<` at |start|, or None. Brackets inside
    parentheses, as in `Foo<(1 > 2)>`, don't count."""
    depth = 0
    parentheses = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            parentheses += 1
        elif c == ")":
            parentheses -= 1
        elif parentheses == 0 and c == "<":
            depth += 1
        elif parentheses == 0 and c == ">":
            depth -= 1
            if depth == 0:
                return i
    return None


def specialization_arguments(cursor: cindex.Cursor, policy: cindex.PrintingPolicy) -> str:
    """A function template specialization's arguments, e.g. `<int32_t>`, or '' for another
    function.

    libclang gives only their canonical types (`<int>`), and none for a method's, so they're read
    from the declaration as clang prints it. clang's DeclPrinter (VisitFunctionDecl) writes the
    name, then the arguments as written, or the deduced ones where none are, then `(` and the
    parameters. Anything else is an error, not a guess."""
    template = cursor.specialized_template
    if template is None or template.kind != K.FUNCTION_TEMPLATE:
        return ""
    declaration = cursor.pretty_printed(policy)
    name = cursor.spelling
    found: list[str] = []
    start = declaration.find(f"{name}<")
    while start >= 0:
        before = declaration[start - 1] if start else " "
        end = closing_angle(declaration, start + len(name))
        if (
            not (before.isalnum() or before == "_")
            and end is not None
            and declaration[end + 1 : end + 2] == "("
        ):
            found.append(declaration[start + len(name) : end + 1])
        start = declaration.find(f"{name}<", start + 1)
    if len(found) != 1:
        raise UnnamedSpecialization(
            f"{name}'s template arguments aren't once after its name in `{declaration}`"
        )
    return found[0]


def entity(
    cursor: cindex.Cursor,
    path: str,
    macros: dict[str, list[Expansion]],
    policy: cindex.PrintingPolicy,
) -> Record | None:
    kind = cursor.kind
    usr = cursor.get_usr()
    if not usr:
        return None
    line = cursor.location.line
    end = cursor.extent.end
    end_line = end.line if checkout_path(end.file) == path else line
    if kind == K.MACRO_DEFINITION:
        return {
            "usr": usr,
            "name": cursor.spelling,
            "signature": None,
            "kind": "macro",
            "file": path,
            "line": line,
            "endLine": end_line,
            "isDefinition": True,
        }
    scope = scope_of(cursor)
    signature = None
    if kind in CLASSES:
        name = qualify(scope, class_name(cursor))
    elif kind in FUNCTIONS:
        # A template is named without its parameters, a specialization with its arguments, and a
        # conversion function by the type it converts to, as written: its spelling has the
        # canonical type (`operator Span<type-parameter-0-0, 18446744073709551615>`).
        if kind == K.CONVERSION_FUNCTION:
            base = f"operator {cursor.result_type.spelling}"
        else:
            base = cursor.spelling
            if kind != K.FUNCTION_TEMPLATE:
                base += specialization_arguments(cursor, policy)
        name = qualify(scope, base)
        parameters = cursor.displayname[len(without_parameters(cursor.displayname)) :]
        signature = qualify(scope, base + parameters)
        if cursor.is_const_method():
            signature += " const"
    else:
        name = qualify(scope, cursor.spelling)
    record: Record = {
        "usr": usr,
        "name": name,
        "signature": signature,
        "kind": KINDS[kind],
        "file": path,
        "line": line,
        "endLine": end_line,
        "isDefinition": cursor.is_definition(),
    }
    parent = cursor.semantic_parent
    if kind == K.FUNCTION_TEMPLATE and parent is not None and parent.kind in CLASSES:
        record["kind"] = "method"
    expansion = macro_at(macros, path, cursor.location.offset)
    if expansion:
        record["macro"] = expansion
    return record


def macro_index(cursors: list[cindex.Cursor]) -> dict[str, list[Expansion]]:
    """For each indexed file, its macro expansions, sorted by where they start."""
    index: collections.defaultdict[str, list[Expansion]] = collections.defaultdict(list)
    for cursor in cursors:
        if cursor.kind != K.MACRO_INSTANTIATION:
            continue
        path = checkout_path(cursor.location.file)
        if not indexed(path):
            continue
        definition = cursor.referenced
        if definition is None:
            continue
        def_file = definition.location.file
        if def_file is None:
            continue
        def_path = checkout_path(def_file) or def_file.name
        index[path].append(
            (
                cursor.extent.start.offset,
                cursor.extent.end.offset,
                {"name": cursor.spelling, "file": def_path, "line": definition.location.line},
            )
        )
    for expansions in index.values():
        expansions.sort(key=lambda e: e[0])
    return index


def macro_at(
    macros: dict[str, list[Expansion]], path: str, offset: int
) -> dict[str, Any] | None:
    """The macro whose expansion at |offset| in |path| declares an entity, if any."""
    expansions = macros.get(path)
    if not expansions:
        return None
    i = bisect.bisect_right(expansions, offset, key=lambda e: e[0]) - 1
    if i >= 0 and expansions[i][0] <= offset <= expansions[i][1]:
        return expansions[i][2]
    return None


def walk(
    cursors: Iterable[cindex.Cursor],
    macros: dict[str, list[Expansion]],
    policy: cindex.PrintingPolicy,
    records: list[Record],
    errors: list[str],
) -> None:
    for cursor in cursors:
        kind = cursor.kind
        if kind == K.MACRO_INSTANTIATION or kind == K.INCLUSION_DIRECTIVE:
            continue
        path = checkout_path(cursor.location.file)
        # A scope declared outside the indexed files has its members there too.
        if not indexed(path):
            continue
        if kind in KINDS:
            try:
                record = entity(cursor, path, macros, policy)
            except UnnamedSpecialization as e:
                errors.append(f"{path}:{cursor.location.line}: {e}")
                record = None
            if record:
                records.append(record)
        if kind in SCOPES:
            walk(cursor.get_children(), macros, policy, records, errors)


def index_tu(task: Task) -> tuple[str, list[str], list[Record]]:
    """Returns the translation unit's path, its errors, and its entities."""
    directory, source, flags = task
    os.chdir(directory)
    where = os.path.join(directory, source)
    try:
        tu = INDEX.parse(
            source, args=flags, options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
        )
    except cindex.TranslationUnitLoadError as e:
        return where, [f"libclang couldn't parse it: {e}"], []
    errors = [str(d) for d in tu.diagnostics if d.severity >= cindex.Diagnostic.Error]
    root = tu.cursor
    if root is None:
        return where, errors + ["libclang gave no cursor for it"], []
    top = list(root.get_children())
    # How specializations' declarations are printed (specialization_arguments): without bodies.
    policy = cindex.PrintingPolicy.create(root)
    policy.set_property(cindex.PrintingPolicyProperty.TerseOutput, 1)
    records: list[Record] = []
    walk(top, macro_index(top), policy, records, errors)
    return where, errors, records


# The parent: run the workers, and merge.


def main() -> int:
    worktree = os.path.dirname(TOOLS)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--checkout",
        default=os.path.join(os.path.dirname(worktree), "firefox-phoenix"),
        help="the checkout to index (default: the reference checkout, ../firefox-phoenix)",
    )
    parser.add_argument("--objdir", default="obj-js-x64", help="relative to the checkout")
    parser.add_argument("--build", default="x64", help="the build's name in the index")
    parser.add_argument("-o", "--output", help="default: ../phoenix-index/BUILD.json")
    parser.add_argument(
        "--path",
        action="append",
        help="index entities declared under this prefix, from the checkout's root "
        "(repeatable; default: js/ and mfbt/)",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    parser.add_argument("--only", help="parse only the inputs whose path contains this")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="write the index even if some translation units have errors",
    )
    parser.add_argument("--libclang", default=DEFAULT_LIBCLANG)
    args = parser.parse_args()

    checkout = os.path.realpath(args.checkout)
    output = args.output or os.path.join(
        os.path.dirname(worktree), "phoenix-index", f"{args.build}.json"
    )
    paths = args.path or list(DEFAULT_PATHS)

    library = os.path.basename(os.path.realpath(args.libclang))
    if not library.endswith(f".so.{BINDINGS_VERSION}"):
        fail(
            f"{args.libclang} is {library}, but the bindings in third_party/clang are "
            f"{BINDINGS_VERSION}: update them (see phoenix-docs/docs/usr-index.md)"
        )
    if git(checkout, "status", "--porcelain"):
        fail(f"{checkout} has uncommitted changes: an index must be of a commit")
    commit = git(checkout, "rev-parse", "--short=12", "HEAD").strip()
    compile_commands = os.path.join(checkout, args.objdir, "compile_commands.json")
    if not os.path.exists(compile_commands):
        fail(
            f"there's no {compile_commands}: in {checkout}, run "
            f"`MOZCONFIG=<its mozconfig> ./mach build-backend -b CompileDB`"
        )

    tasks = tasks_of(compile_commands)
    if args.only:
        tasks = [t for t in tasks if args.only in os.path.join(t[0], t[1])]
    print(
        f"Indexing {len(tasks)} translation units of {checkout} ({commit}), "
        f"{args.objdir}, with {args.jobs} jobs",
        file=sys.stderr,
    )

    start = time.time()
    # Keyed by USR, file, line, kind and whether it's a definition.
    entities: dict[tuple[str, str, int, str, bool], Record] = {}
    failures: list[tuple[str, list[str]]] = []
    with multiprocessing.Pool(
        args.jobs, initializer=init_worker, initargs=(args.libclang, checkout, paths)
    ) as pool:
        for done, (source, errors, records) in enumerate(
            pool.imap_unordered(index_tu, tasks), start=1
        ):
            if errors:
                failures.append((source, errors))
            for record in records:
                key = (
                    record["usr"],
                    record["file"],
                    record["line"],
                    record["kind"],
                    record["isDefinition"],
                )
                entities.setdefault(key, record)
            if done % 100 == 0:
                print(f"  {done}/{len(tasks)}, {time.time() - start:.0f}s", file=sys.stderr)

    for source, errors in failures:
        print(f"{source}: {len(errors)} errors, e.g. {errors[0]}", file=sys.stderr)
    if failures and not args.keep_going:
        fail(f"{len(failures)} translation units have errors, so the index would be incomplete")

    records = sorted(
        entities.values(), key=lambda r: (r["file"], r["line"], r["kind"], r["usr"], r["isDefinition"])
    )
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    temporary = f"{output}.tmp"
    with open(temporary, "w") as f:
        f.write(f'{{"build": {json.dumps(args.build)}, "commit": {json.dumps(commit)}, "entities": [\n')
        f.write(",\n".join(json.dumps(r) for r in records))
        f.write("\n]}\n")
    os.replace(temporary, output)

    kinds = collections.Counter(r["kind"] for r in records)
    print(
        f"{len(records)} entities in {len({r['file'] for r in records})} files, "
        f"from {len(tasks)} translation units in {time.time() - start:.0f}s, written to {output}",
        file=sys.stderr,
    )
    print("  " + ", ".join(f"{n} {k}" for k, n in kinds.most_common()), file=sys.stderr)
    print(f"  {sum('macro' in r for r in records)} declared by a macro", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
