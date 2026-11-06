#!/usr/bin/env python3
"""Tests of usr-index.py, on the fixture in usr-index/.

  python3 phoenix-tools/tests/test-usr-index.py

Each run copies the fixture into a scratch git checkout, with an objdir holding a unified bundle,
a dist/include symlink to a header, and a hand-written compile database, then indexes it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

TESTS = os.path.dirname(os.path.abspath(__file__))
INDEXER = os.path.join(os.path.dirname(TESTS), "usr-index.py")
FIXTURE = os.path.join(TESTS, "usr-index")
CLANG = os.path.expanduser("~/.mozbuild/clang/bin/clang++")
BUNDLE = "Unified_cpp_fixture0.cpp"

# An entity of the index, as parsed from JSON.
Entity = dict[str, Any]

checks = 0
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(message)


def git(checkout: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", checkout, "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        check=True,
        capture_output=True,
    )


def index(checkout: str, output: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            INDEXER,
            "--checkout",
            checkout,
            "--objdir",
            "obj",
            "--build",
            "test",
            "--path",
            "inc/",
            "--path",
            "src/",
            "--jobs",
            "2",
            "-o",
            output,
            *args,
        ],
        capture_output=True,
        text=True,
    )


def make_checkout(root: str) -> str:
    checkout = os.path.join(root, "checkout")
    shutil.copytree(FIXTURE, checkout)
    with open(os.path.join(checkout, ".gitignore"), "w") as f:
        f.write("obj/\n")
    git(checkout, "init", "-q")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "fixture")

    obj = os.path.join(checkout, "obj")
    os.makedirs(os.path.join(obj, "dist", "include"))
    os.symlink(
        os.path.join(checkout, "inc", "shapes.h"),
        os.path.join(obj, "dist", "include", "shapes.h"),
    )
    with open(os.path.join(obj, BUNDLE), "w") as f:
        for member in ("more.cpp", "other.cpp"):
            f.write(f'#include "{os.path.join(checkout, "src", member)}"\n')
    flags = f"{CLANG} -std=gnu++20 -I {obj}/dist/include -o /dev/null -c"
    entries = [
        # A flag for this file alone comes after its input.
        {
            "directory": obj,
            "file": f"{checkout}/src/shapes.cpp",
            "command": f"{flags} {checkout}/src/shapes.cpp -Wno-unused-function",
        },
        {"directory": obj, "file": f"{obj}/{BUNDLE}", "command": f"{flags} {BUNDLE}"},
        {"directory": obj, "file": f"{checkout}/src/more.cpp", "command": f"{flags} {BUNDLE}"},
        {"directory": obj, "file": f"{checkout}/src/other.cpp", "command": f"{flags} {BUNDLE}"},
    ]
    with open(os.path.join(obj, "compile_commands.json"), "w") as f:
        json.dump(entries, f)
    return checkout


def line_of(checkout: str, path: str, text: str) -> int:
    with open(os.path.join(checkout, path)) as f:
        for number, line in enumerate(f, start=1):
            if text in line:
                return number
    raise AssertionError(f"{text!r} isn't in {path}")


def test_index(checkout: str, root: str) -> None:
    output = os.path.join(root, "test.json")
    run = index(checkout, output)
    check(run.returncode == 0, f"the indexer failed:\n{run.stderr}")
    if run.returncode != 0:
        return
    check(
        "Indexing 2 translation units" in run.stderr,
        f"expected 2 translation units, one of them the bundle:\n{run.stderr}",
    )
    with open(output) as f:
        result: dict[str, Any] = json.load(f)
    head = subprocess.run(
        ["git", "-C", checkout, "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    check(result["build"] == "test", f"build is {result['build']}")
    check(result["commit"] == head, f"commit is {result['commit']}, not {head}")
    entities: list[Entity] = result["entities"]

    def find(name: str, **fields: Any) -> list[Entity]:
        return [
            e for e in entities if e["name"] == name and all(e[k] == v for k, v in fields.items())
        ]

    def one(name: str, **fields: Any) -> Entity:
        found = find(name, **fields)
        check(len(found) == 1, f"{name} {fields}: found {found}")
        return found[0] if found else {}

    h = "inc/shapes.h"
    check(
        all(e["file"].startswith(("inc/", "src/")) for e in entities),
        "an entity outside inc/ and src/, e.g. through the dist/include symlink: "
        + str([e["file"] for e in entities if not e["file"].startswith(("inc/", "src/"))][:3]),
    )

    one("geo::Red", kind="enumerator", file=h)
    one("geo::Kind::Circle", kind="enumerator", file=h)
    one("geo::Point::x", kind="field", file=h, line=line_of(checkout, h, "int x;"))
    one("geo::Shape", kind="class", file=h, isDefinition=True, line=line_of(checkout, h, "class Shape"))

    declared = one("geo::Shape::Shape", kind="constructor", file=h, isDefinition=False)
    defined = one("geo::Shape::Shape", kind="constructor", file="src/shapes.cpp", isDefinition=True)
    check(declared.get("usr") == defined.get("usr"), "the constructor's declaration and definition differ in USR")

    area = one("geo::Shape::circleArea", file=h, line=line_of(checkout, h, "DECLARE_AREA(circle)"))
    check(
        area.get("macro")
        == {"name": "DECLARE_AREA", "file": h, "line": line_of(checkout, h, "#define DECLARE_AREA")},
        f"circleArea's macro is {area.get('macro')}",
    )
    area_definition = one("geo::Shape::circleArea", file="src/more.cpp", isDefinition=True)
    check("macro" not in area_definition, "circleArea's definition has a macro")
    check(
        area_definition.get("signature") == "geo::Shape::circleArea() const",
        f"circleArea's signature is {area_definition.get('signature')}",
    )

    sizes = find("geo::Shape::size", file=h)
    check(
        sorted(e["signature"] for e in sizes) == ["geo::Shape::size()", "geo::Shape::size() const"],
        f"size's overloads: {[e['signature'] for e in sizes]}",
    )
    check(len({e["usr"] for e in sizes}) == 2, "size's overloads share a USR")

    call = one("geo::Shape::operator()", file="src/other.cpp", isDefinition=True)
    check(
        call.get("signature") == "geo::Shape::operator()(int) const",
        f"operator()'s signature is {call.get('signature')}",
    )

    template = one("geo::twice", file=h, isDefinition=True)
    check(template.get("kind") == "function", f"twice's kind is {template.get('kind')}")
    specialization = one("geo::twice<int>", file="src/shapes.cpp", isDefinition=True)
    one("geo::twice<int>", file=h, isDefinition=False)
    check(
        template.get("usr") != specialization.get("usr"),
        "the template and its explicit specialization share a USR",
    )

    check(
        specialization.get("signature") == "geo::twice<int>(int)",
        f"the specialization's signature is {specialization.get('signature')}",
    )

    box = one("geo::Box", file=h, isDefinition=True)
    one("geo::Box::value", kind="field", file=h)
    box_int = one("geo::Box<int>", file=h, isDefinition=True)
    one("geo::Box<int>::count", kind="field", file=h)
    check(box.get("usr") != box_int.get("usr"), "the class template and its specialization share a USR")

    # A function specialization's arguments are as written, so a typedef stays.
    count = one("geo::half<Count>", file=h)
    check(
        count.get("signature") == "geo::half<Count>(Count)",
        f"half<Count>'s signature is {count.get('signature')}",
    )
    one("geo::half<double>", file=h, line=line_of(checkout, h, "double half(double v);"))
    from_macro = one("geo::half<float>", file=h)
    check(
        (from_macro.get("macro") or {}).get("name") == "DECLARE_HALF",
        f"half<float>'s macro is {from_macro.get('macro')}",
    )
    member = one("geo::Holder::is<Shape>", kind="method", file=h)
    check(
        member.get("signature") == "geo::Holder::is<Shape>() const",
        f"is<Shape>'s signature is {member.get('signature')}",
    )
    conversion = one("geo::Wrapper::operator geo::Box<T>", kind="method", file=h)
    check(
        conversion.get("signature") == "geo::Wrapper::operator geo::Box<T>() const",
        f"the conversion function's signature is {conversion.get('signature')}",
    )
    canonical = [e["name"] for e in entities if "type-parameter" in e["name"]]
    check(not canonical, f"names with clang's canonical spellings: {canonical}")

    # `= default` and `= delete` are definitions. clang makes Pair() a body only in shapes.cpp,
    # which uses it, so libclang alone would give it a record with each isDefinition.
    for name, signature, definition in [
        ("geo::Pair::Pair", "geo::Pair::Pair()", True),
        ("geo::Pair::Pair", "geo::Pair::Pair(const Pair &)", True),
        ("geo::release", "geo::release(int *)", True),
        ("geo::release", "geo::release(T *)", True),
        ("geo::scale", "geo::scale(int)", False),
    ]:
        record = one(name, signature=signature, file=h)
        check(
            record.get("isDefinition") is definition,
            f"{signature}'s isDefinition is {record.get('isDefinition')}",
        )

    scales = find("geo::scale", file=h)
    check(
        sorted(e["signature"] for e in scales) == ["geo::scale(double)", "geo::scale(int)"],
        f"scale's overloads: {[e['signature'] for e in scales]}",
    )

    one("geo::(anonymous namespace)::hidden", kind="variable", file=h)
    one("DECLARE_AREA", kind="macro", file=h, isDefinition=True)
    one("SHAPES_H", kind="macro", file=h)
    one("helper", kind="function", file="src/more.cpp")


def test_refuses_uncommitted_changes(checkout: str, root: str) -> None:
    with open(os.path.join(checkout, "inc", "shapes.h"), "a") as f:
        f.write("// an edit\n")
    result = index(checkout, os.path.join(root, "dirty.json"))
    check(
        result.returncode != 0 and "uncommitted changes" in result.stderr,
        f"the indexer ran on a checkout with uncommitted changes:\n{result.stderr}",
    )
    git(checkout, "checkout", "--", "inc/shapes.h")


def test_fails_on_errors(checkout: str, root: str) -> None:
    with open(os.path.join(checkout, "src", "broken.cpp"), "w") as f:
        f.write("int broken( {\n")
    git(checkout, "add", "src/broken.cpp")
    git(checkout, "commit", "-q", "-m", "broken")
    database = os.path.join(checkout, "obj", "compile_commands.json")
    with open(database) as f:
        entries: list[dict[str, str]] = json.load(f)
    entries.append(
        {
            "directory": entries[0]["directory"],
            "file": f"{checkout}/src/broken.cpp",
            "command": f"{CLANG} -std=gnu++20 -o /dev/null -c {checkout}/src/broken.cpp",
        }
    )
    with open(database, "w") as f:
        json.dump(entries, f)
    output = os.path.join(root, "broken.json")
    result = index(checkout, output, "--only", "broken.cpp")
    check(
        result.returncode != 0 and "broken.cpp" in result.stderr and "have errors" in result.stderr,
        f"the indexer didn't fail on a translation unit with errors:\n{result.stderr}",
    )
    check(not os.path.exists(output), "the indexer wrote an index despite errors")


def test_fails_on_unread_arguments(checkout: str, root: str) -> None:
    # A parenthesized name is printed `(half<short>)(short v)`: no `(` after the arguments.
    with open(os.path.join(checkout, "src", "paren.cpp"), "w") as f:
        f.write('#include "shapes.h"\nnamespace geo {\ntemplate <>\nshort (half<short>)(short v);\n}\n')
    git(checkout, "add", "src/paren.cpp")
    git(checkout, "commit", "-q", "-m", "paren")
    database = os.path.join(checkout, "obj", "compile_commands.json")
    with open(database) as f:
        entries: list[dict[str, str]] = json.load(f)
    entries.append(
        {
            "directory": entries[0]["directory"],
            "file": f"{checkout}/src/paren.cpp",
            "command": f"{CLANG} -std=gnu++20 -I {checkout}/inc -o /dev/null -c {checkout}/src/paren.cpp",
        }
    )
    with open(database, "w") as f:
        json.dump(entries, f)
    output = os.path.join(root, "paren.json")
    result = index(checkout, output, "--only", "paren.cpp")
    check(
        result.returncode != 0 and "template arguments" in result.stderr,
        f"the indexer didn't fail on arguments it couldn't read:\n{result.stderr}",
    )
    check(not os.path.exists(output), "the indexer wrote an index despite unread arguments")


def main() -> int:
    with tempfile.TemporaryDirectory() as root:
        checkout = make_checkout(root)
        test_index(checkout, root)
        test_refuses_uncommitted_changes(checkout, root)
        test_fails_on_errors(checkout, root)
        test_fails_on_unread_arguments(checkout, root)
    if failures:
        print(f"{len(failures)} of {checks} checks failed:")
        for message in failures:
            print(f"- {message}")
        return 1
    print(f"{checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
