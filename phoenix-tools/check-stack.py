#!/usr/bin/env python3
"""Check that the Phoenix branches keep their structure.

  check-stack.py [--worktree] [--reference REF] [--dev REF]

The structure (see phoenix-docs/docs/branches.md):

  phoenix-pin/<sha>   the upstream commit cachet-lean translates (a tag)
  phoenix             the pin plus [BUG FIX] commits only
  phoenix-dev         phoenix plus instrumentation, docs and tools

Checks:

1. phoenix's first-parent history is a run of [BUG FIX] commits on a commit
   tagged phoenix-pin/*.
2. Each [BUG FIX] commit changes Firefox files, adds one report
   phoenix-docs/bug-fixes/NNNN-*.md numbered after the previous commit's (gaps
   are fine: a fix upstream has made is dropped when the pin moves), names it
   in its message, and touches nothing else outside phoenix-tools/repro/.
3. phoenix is an ancestor of phoenix-dev, and no commit above it is a
   [BUG FIX].
4. Every change from phoenix to phoenix-dev outside phoenix-docs/ and
   phoenix-tools/ is compiled only with --enable-phoenix. This is a heuristic,
   from the text of the change:
   - C and C++: added code must be inside #ifdef JS_PHOENIX (or a condition
     that requires JS_PHOENIX), or be a directive that names JS_PHOENIX.
     Removed lines are allowed only for a directive replaced by one that names
     JS_PHOENIX. Blank and comment lines are ignored. A new file is fine if a
     moz.build compiles it only under CONFIG["JS_PHOENIX"].
   - moz.build: added lines must be in an `if CONFIG["JS_PHOENIX"]:` block.
   - moz.configure: added blocks must mention phoenix, and nothing is removed.
   - Anything else is an error, except the files in ALLOWED.

With --worktree, check 4 also covers the uncommitted changes of the current
worktree, which must be on the dev branch. Exits 1 if a check fails.
"""

import argparse
import difflib
import os
import re
import subprocess
import sys

PIN_TAG_PREFIX = "phoenix-pin/"
FIX_PREFIX = "[BUG FIX]"
REPORT_RE = re.compile(r"^phoenix-docs/bug-fixes/(\d{4})-[^/]+\.md$")
OURS = ("phoenix-docs/", "phoenix-tools/")
CXX_EXTENSIONS = (".h", ".hpp", ".c", ".cc", ".cpp", ".mm")

# Firefox files that may change without the guard. Neither is built.
ALLOWED = {
    "CLAUDE.md": "loads phoenix-docs/CLAUDE.md for agents",
}

DIRECTIVE_RE = re.compile(r"^\s*#\s*(\w+)\s*(.*)$")

errors = []


def error(message):
    errors.append(message)


def git(*args, check=True):
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result


def git_out(*args):
    return git(*args).stdout


def file_at(rev, path):
    result = git("show", f"{rev}:{path}", check=False)
    return result.stdout.splitlines() if result.returncode == 0 else None


def worktree_file(root, path):
    full = os.path.join(root, path)
    if not os.path.exists(full):
        return None
    with open(full, errors="replace") as f:
        return f.read().splitlines()


# Checks 1 and 2: the reference branch.


def check_reference(reference):
    commits = []
    rev = git_out("rev-parse", reference).strip()
    while True:
        line = git_out("log", "-1", "--format=%H %P%x00%s", rev).strip()
        ids, subject = line.split("\0", 1)
        sha, *parents = ids.split()
        if not subject.startswith(FIX_PREFIX):
            break
        if len(parents) != 1:
            error(f"{sha[:12]} on {reference} is a merge")
            break
        commits.append((sha, subject))
        rev = parents[0]
    pin = rev
    tags = [t for t in git_out("tag", "--points-at", pin).split() if t.startswith(PIN_TAG_PREFIX)]
    if not tags:
        subject = git_out("log", "-1", "--format=%s", pin).strip()
        error(
            f"{reference}'s [BUG FIX] commits sit on {pin[:12]} ({subject!r}), "
            f"which isn't tagged {PIN_TAG_PREFIX}*: is a commit that isn't a "
            f"[BUG FIX] on {reference}, or the pin untagged?"
        )
    commits.reverse()

    last = 0
    for sha, subject in commits:
        last = check_fix_commit(sha, subject, last)
    return pin, tags, commits


def check_fix_commit(sha, subject, last):
    """Returns the commit's report number, or |last| if it has none."""
    short = f"{sha[:12]} ({subject})"
    paths = git_out("diff-tree", "--no-commit-id", "-r", "--name-status", sha).splitlines()
    reports = []
    firefox = []
    for line in paths:
        status, path = line.split("\t", 1)
        match = REPORT_RE.match(path)
        if match:
            if status != "A":
                error(f"{short} changes the existing report {path}")
            reports.append((int(match.group(1)), path))
        elif path.startswith("phoenix-tools/repro/"):
            pass
        elif path.startswith(OURS):
            error(f"{short} touches {path}: a [BUG FIX] commit holds only the fix and its report")
        else:
            firefox.append(path)
    if not firefox:
        error(f"{short} changes no Firefox file")
    if len(reports) != 1:
        error(f"{short} adds {len(reports)} reports in phoenix-docs/bug-fixes/, not one")
        return last
    report_number, report = reports[0]
    if report_number <= last:
        error(f"{short} adds report {report_number:04d}, after report {last:04d}")
    if report not in git_out("log", "-1", "--format=%B", sha):
        error(f"{short}'s message doesn't name its report, {report}")
    return report_number


# Check 3: the dev branch.


def check_dev(reference, dev):
    if git("merge-base", "--is-ancestor", reference, dev, check=False).returncode != 0:
        error(f"{reference} isn't an ancestor of {dev}: rebase {dev} onto {reference}")
        return False
    for line in git_out("log", "--format=%h %s", f"{reference}..{dev}").splitlines():
        sha, subject = line.split(" ", 1)
        if subject.startswith(FIX_PREFIX):
            error(
                f"{sha} on {dev} is a [BUG FIX]: move it to {reference}, "
                f"then rebase {dev} onto {reference}"
            )
    return True


# Check 4: instrumentation is guarded.


def is_neutral(line):
    stripped = line.strip()
    return not stripped or stripped == "*" or stripped.startswith(("//", "/*", "* ", "*/"))


def requires_phoenix(condition):
    """Whether a #if condition can only hold when JS_PHOENIX is defined."""
    condition = re.sub(r"/\*.*?\*/|//.*", "", condition).strip()
    if "||" in condition or "!" in condition:
        return False
    return bool(re.search(r"\bdefined\s*\(?\s*JS_PHOENIX\b", condition))


def guard_map(lines):
    """For each line: (directive, mentions JS_PHOENIX, inside a JS_PHOENIX-only
    region, the directive belongs to a JS_PHOENIX conditional)."""
    stack = []  # [phoenix_only_branch, condition_names_phoenix]
    result = []
    for line in lines:
        match = DIRECTIVE_RE.match(line)
        inside = any(only for only, _ in stack)
        if not match:
            result.append((False, False, inside, False))
            continue
        name, rest = match.groups()
        names_phoenix = "JS_PHOENIX" in rest
        belongs = names_phoenix
        if name in ("if", "ifdef", "ifndef"):
            condition = f"defined({rest})" if name == "ifdef" else rest
            only = name != "ifndef" and requires_phoenix(condition)
            stack.append([only, names_phoenix])
        elif name in ("elif", "else"):
            if stack:
                belongs = belongs or stack[-1][1]
                stack[-1][0] = name == "elif" and requires_phoenix(rest)
        elif name == "endif":
            if stack:
                belongs = belongs or stack[-1][1]
                stack.pop()
        result.append((True, names_phoenix, inside, belongs))
    return result


def include_guard(lines):
    """The line numbers of a header's include guard, if it has one."""
    directives = [(i, DIRECTIVE_RE.match(l)) for i, l in enumerate(lines)]
    directives = [(i, m) for i, m in directives if m]
    if len(directives) < 3:
        return set()
    (i0, m0), (i1, m1), (iz, mz) = directives[0], directives[1], directives[-1]
    if (
        m0.group(1) == "ifndef"
        and m1.group(1) == "define"
        and m1.group(2).split()[:1] == m0.group(2).split()[:1]
        and mz.group(1) == "endif"
    ):
        return {i0, i1, iz}
    return set()


def phoenix_build_files(read_new, moz_builds):
    """The files that a moz.build compiles only under CONFIG["JS_PHOENIX"]."""
    files = set()
    for path in moz_builds:
        lines = read_new(path) or []
        for start, end in phoenix_blocks(lines):
            for line in lines[start:end]:
                for name in re.findall(r"[\"']([^\"']+\.\w+)[\"']", line):
                    files.add(os.path.normpath(os.path.join(os.path.dirname(path), name)))
    return files


def phoenix_blocks(lines):
    blocks = []
    for i, line in enumerate(lines):
        if re.match(r"^\s*if\s+CONFIG\[\s*[\"']JS_PHOENIX[\"']\s*\]\s*:", line):
            indent = len(line) - len(line.lstrip())
            end = i + 1
            while end < len(lines) and (
                not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent
            ):
                end += 1
            blocks.append((i, end))
    return blocks


def changed_lines(old, new):
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != "equal":
            yield list(range(a0, a1)), list(range(b0, b1))


def check_cxx(path, old, new, where):
    guards = guard_map(new)
    neutral = include_guard(new) if old is None else set()
    for removed, added in changed_lines(old or [], new):
        adds_phoenix_directive = any(guards[i][0] and guards[i][1] for i in added)
        for i in removed:
            line = old[i]
            if is_neutral(line):
                continue
            if DIRECTIVE_RE.match(line) and adds_phoenix_directive:
                continue
            error(f"{where}{path}: removes `{line.strip()}`, which builds without --enable-phoenix")
        for i in added:
            line = new[i]
            directive, names_phoenix, inside, belongs = guards[i]
            if i in neutral or is_neutral(line) or inside:
                continue
            if directive and (names_phoenix or belongs):
                continue
            error(
                f"{where}{path}:{i + 1}: `{line.strip()}` isn't inside #ifdef JS_PHOENIX"
            )


def check_moz_build(path, old, new, where):
    inside = set()
    for start, end in phoenix_blocks(new):
        inside.update(range(start, end))
    for removed, added in changed_lines(old or [], new):
        for i in removed:
            if old[i].strip():
                error(f"{where}{path}: removes `{old[i].strip()}`")
        for i in added:
            if new[i].strip() and i not in inside:
                error(f'{where}{path}:{i + 1}: `{new[i].strip()}` isn\'t under if CONFIG["JS_PHOENIX"]')


def check_moz_configure(path, old, new, where):
    for removed, added in changed_lines(old or [], new):
        if any(old[i].strip() for i in removed):
            error(f"{where}{path}: removes lines {removed[0] + 1}-{removed[-1] + 1}")
        if added and not any("phoenix" in new[i].lower() for i in added):
            error(f"{where}{path}:{added[0] + 1}: adds lines that don't mention phoenix")


def check_guards(reference, paths, read_new, where):
    moz_builds = [p for p in paths if os.path.basename(p) == "moz.build"]
    guarded_files = phoenix_build_files(read_new, moz_builds)
    for path in sorted(paths):
        if path.startswith(OURS):
            continue
        if path in ALLOWED:
            continue
        old = file_at(reference, path)
        new = read_new(path)
        if new is None:
            error(f"{where}{path}: deleted")
        elif path.endswith(CXX_EXTENSIONS):
            if old is None and os.path.normpath(path) in guarded_files:
                continue
            check_cxx(path, old, new, where)
        elif os.path.basename(path) == "moz.build":
            check_moz_build(path, old, new, where)
        elif os.path.basename(path) == "moz.configure":
            check_moz_configure(path, old, new, where)
        else:
            error(f"{where}{path}: changed, and it isn't code that --enable-phoenix can guard")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reference", default="phoenix")
    parser.add_argument("--dev", default="phoenix-dev")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="also check the current worktree's uncommitted changes",
    )
    args = parser.parse_args()

    root = git_out("rev-parse", "--show-toplevel").strip()
    os.chdir(root)

    pin, tags, fixes = check_reference(args.reference)
    print(f"pin: {pin[:12]} ({', '.join(tags) or 'untagged'})")
    print(f"{args.reference}: {len(fixes)} [BUG FIX] commits")
    for sha, subject in fixes:
        print(f"  {sha[:12]} {subject}")

    if check_dev(args.reference, args.dev):
        dev_commits = git_out("rev-list", "--count", f"{args.reference}..{args.dev}").strip()
        print(f"{args.dev}: {dev_commits} commits above {args.reference}")
        paths = git_out("diff", "--name-only", args.reference, args.dev).splitlines()
        check_guards(
            args.reference, paths, lambda p: file_at(args.dev, p), f"{args.dev}: "
        )

    uncommitted = git_out("diff", "--name-only", "HEAD").splitlines() + git_out(
        "ls-files", "--others", "--exclude-standard"
    ).splitlines()
    if args.worktree:
        branch = git_out("branch", "--show-current").strip()
        if branch != args.dev:
            error(f"--worktree: this worktree is on {branch or 'a detached HEAD'}, not {args.dev}")
        else:
            paths = git_out("diff", "--name-only", args.reference).splitlines()
            paths += git_out("ls-files", "--others", "--exclude-standard").splitlines()
            check_guards(
                args.reference, set(paths), lambda p: worktree_file(root, p), "worktree: "
            )
            print(f"worktree: checked {len(uncommitted)} files with uncommitted changes")
    elif uncommitted:
        print(f"({len(uncommitted)} files with uncommitted changes not checked: use --worktree)")

    if errors:
        print(f"\n{len(errors)} problems:")
        for message in errors:
            print(f"  {message}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
