#!/usr/bin/env python3
"""Make a sysroot, for cross-compiling to Linux on another architecture, from Debian's packages.

  sysroot.py --arch ARCH [--dist NAME] [--snapshot TIMESTAMP] [--mirror URL] [--gcc N]
             [--package NAME]... [--keyring FILE | --no-signature-check] [-o DIR] [--force]

A sysroot is a directory laid out like the target system's root, holding only the headers and
libraries the compiler and linker read (a mozconfig's --with-sysroot). This one is made like
Mozilla's (taskcluster/scripts/misc/build-sysroot.sh), but without root: it downloads a fixed list
of packages from a snapshot of Debian's archive, checks them against the release's signed index,
and unpacks their lib/, usr/include/ and usr/lib/. It doesn't resolve dependencies: the list is
what a JS shell needs, and --package adds to it. How the sysroots are used is in
phoenix-docs/docs/backends.md.
"""

import argparse
import hashlib
import io
import json
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, NoReturn

# The snapshot Mozilla's build-sysroot.sh uses for releases after buster: Debian 12.0.
DEFAULT_SNAPSHOT = "20230611T210420Z"
DEFAULT_DIST = "bookworm"
# Each release's GCC, whose libgcc and libstdc++ the sysroot takes.
GCC_OF_DIST = {"bullseye": "10", "bookworm": "12", "trixie": "14"}
# Where the debian-archive-keyring package puts Debian's keys.
DEFAULT_KEYRINGS = (
    "/usr/share/keyrings/debian-archive-keyring.pgp",
    "/usr/share/keyrings/debian-archive-keyring.gpg",
)
# What's unpacked, as prefixes of paths from the root, and what isn't, under those.
KEPT = ("lib/", "usr/include/", "usr/lib/")
DROPPED = ("usr/lib/debug/",)
# Written at the sysroot's root: where it came from.
MANIFEST = "phoenix-sysroot.json"
ATTEMPTS = 3
TIMEOUT = 120

# A Release file's fields, and its SHA256 list: each index's digest and size, by path.
Release = tuple[dict[str, str], dict[str, tuple[str, int]]]
# A package's fields in the index (Packages).
Stanza = dict[str, str]


def fail(message: str) -> NoReturn:
    sys.exit(f"sysroot.py: {message}")


def log(message: str) -> None:
    print(message, file=sys.stderr)


def base_packages(gcc: str) -> list[str]:
    return [
        "libc6",
        "libc6-dev",
        "linux-libc-dev",
        "libgcc-s1",
        f"libgcc-{gcc}-dev",
        "libstdc++6",
        f"libstdc++-{gcc}-dev",
        "zlib1g",
        "zlib1g-dev",
    ]


def fetch(url: str) -> bytes:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if (e.code < 500 and e.code != 429) or attempt == ATTEMPTS:
                fail(f"can't fetch {url}: HTTP {e.code}")
        except OSError as e:
            if not url.startswith(("http:", "https:")) or attempt == ATTEMPTS:
                fail(f"can't fetch {url}: {e}")
        log(f"retrying {url}")
        time.sleep(10 * attempt)
    raise AssertionError("unreachable")


def check_digest(data: bytes, sha256: str, size: int, what: str) -> None:
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        fail(f"{what} doesn't match the checksum the release gives it")


def verified_release(signed: bytes, keyring: str) -> str:
    """The text InRelease signs, if a key of `keyring` (an absolute path) signed it and no
    signature of it is bad."""
    with tempfile.TemporaryDirectory() as scratch:
        signed_path = os.path.join(scratch, "InRelease")
        text_path = os.path.join(scratch, "Release")
        with open(signed_path, "wb") as f:
            f.write(signed)
        result = subprocess.run(
            [
                "gpgv",
                "--homedir",
                scratch,
                "--status-fd",
                "1",
                "--keyring",
                keyring,
                "--output",
                text_path,
                signed_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        status = [
            line.split()[1]
            for line in result.stdout.splitlines()
            if line.startswith("[GNUPG:] ") and len(line.split()) > 1
        ]
        if (
            "VALIDSIG" not in status
            or "BADSIG" in status
            or "REVKEYSIG" in status
            or not os.path.exists(text_path)
        ):
            fail(f"InRelease's signature doesn't check out with {keyring}:\n{result.stderr.strip()}")
        with open(text_path, encoding="utf-8") as f:
            return f.read()


def parse_release(text: str) -> Release:
    fields: dict[str, str] = {}
    digests: dict[str, tuple[str, int]] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith(" "):
            if section == "SHA256":
                digest, size, path = line.split()
                digests[path] = (digest, int(size))
        elif ":" in line:
            section, _, value = line.partition(":")
            fields[section] = value.strip()
    return fields, digests


def parse_packages(text: str, wanted: set[str]) -> dict[str, Stanza]:
    found: dict[str, Stanza] = {}
    for block in text.split("\n\n"):
        stanza: Stanza = {}
        for line in block.splitlines():
            if line and not line[0].isspace() and ":" in line:
                key, _, value = line.partition(":")
                stanza[key] = value.strip()
        name = stanza.get("Package")
        if name is not None and name in wanted:
            if name in found:
                fail(f"the index lists {name} twice")
            found[name] = stanza
    return found


def data_tar(deb: bytes, name: str) -> tarfile.TarFile:
    """A .deb's data.tar. A .deb is an ar archive of debian-binary, control.tar.* and
    data.tar.*."""
    if not deb.startswith(b"!<arch>\n"):
        fail(f"{name} isn't a .deb")
    offset = 8
    while offset + 60 <= len(deb):
        header = deb[offset : offset + 60]
        member = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii"))
        start = offset + 60
        if member.startswith("data.tar"):
            try:
                return tarfile.open(fileobj=io.BytesIO(deb[start : start + size]), mode="r:*")
            except tarfile.TarError as e:
                fail(f"can't read {name}'s {member}: {e}")
        offset = start + size + size % 2
    fail(f"{name} has no data.tar")


def kept(path: str) -> bool:
    path += "/"
    return path.startswith(KEPT) and not path.startswith(DROPPED)


def unpack(tar: tarfile.TarFile, root: str) -> int:
    """Extracts the kept members into `root`, with absolute symlinks made relative, so that they
    point into the sysroot instead of this machine's root. Returns how many files it extracted."""
    members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        path = os.path.normpath(member.name)
        if not kept(path):
            continue
        member.name = path
        if member.issym() and member.linkname.startswith("/"):
            member.linkname = os.path.relpath(
                member.linkname.lstrip("/"), os.path.dirname(path) or "."
            )
        elif member.islnk():
            member.linkname = os.path.normpath(member.linkname)
        members.append(member)
    tar.extractall(root, members=members, filter="data")
    return sum(1 for m in members if m.isfile())


def remove_empty_directories(root: str) -> None:
    for directory, _, _ in os.walk(root, topdown=False):
        if directory != root and not os.listdir(directory):
            os.rmdir(directory)


def install(staging: str, output: str) -> None:
    if not os.path.lexists(output):
        os.rename(staging, output)
        return
    old = f"{staging}.old"
    os.rename(output, old)
    os.rename(staging, output)
    shutil.rmtree(old)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--arch", required=True, help="Debian's name for the architecture, e.g. armhf or arm64"
    )
    parser.add_argument(
        "--dist", default=DEFAULT_DIST, help=f"the Debian release (default: {DEFAULT_DIST})"
    )
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help=f"the snapshot.debian.org timestamp (default: {DEFAULT_SNAPSHOT}, Debian 12.0)",
    )
    parser.add_argument("--mirror", help="the archive's URL, in place of the snapshot's")
    parser.add_argument(
        "--gcc", help="the GCC whose libgcc and libstdc++ to take (default: the release's)"
    )
    parser.add_argument(
        "--package", action="append", default=[], help="another package to unpack (repeatable)"
    )
    signature = parser.add_mutually_exclusive_group()
    signature.add_argument(
        "--keyring",
        help="the keys to check the release's signature with "
        "(default: Debian's, from the debian-archive-keyring package)",
    )
    signature.add_argument(
        "--no-signature-check",
        action="store_true",
        help="don't check the release's signature: trust the connection to the archive",
    )
    parser.add_argument("-o", "--output", help="default: ~/.mozbuild/sysroot-ARCH-linux-gnu")
    parser.add_argument(
        "--force", action="store_true", help="replace a sysroot this script made before"
    )
    args = parser.parse_args()

    gcc: str | None = args.gcc or GCC_OF_DIST.get(args.dist)
    if gcc is None:
        fail(f"which GCC {args.dist} has isn't known: pass --gcc")
    mirror: str = (args.mirror or f"https://snapshot.debian.org/archive/debian/{args.snapshot}")
    mirror = mirror.rstrip("/")
    output = os.path.abspath(
        args.output or os.path.expanduser(f"~/.mozbuild/sysroot-{args.arch}-linux-gnu")
    )
    if os.path.lexists(output):
        if not args.force:
            fail(f"{output} exists: pass --force to replace it")
        if not os.path.isfile(os.path.join(output, MANIFEST)):
            fail(f"{output} has no {MANIFEST}, so this script didn't make it: remove it yourself")

    keyring: str | None = None
    if not args.no_signature_check:
        keyring = args.keyring or next((k for k in DEFAULT_KEYRINGS if os.path.exists(k)), None)
        if keyring is None:
            fail(
                "Debian's keys aren't installed, so the release's signature can't be checked. "
                "Install them (`sudo apt install debian-archive-keyring`), pass --keyring FILE, "
                "or pass --no-signature-check to trust the connection to the archive instead"
            )
        if not os.path.isfile(keyring):
            fail(f"there's no {keyring}")
        if shutil.which("gpgv") is None:
            fail("gpgv, which checks the release's signature, isn't installed")

    dists = f"{mirror}/dists/{args.dist}"
    if keyring is None:
        text = fetch(f"{dists}/Release").decode("utf-8")
    else:
        text = verified_release(fetch(f"{dists}/InRelease"), os.path.abspath(keyring))
    fields, digests = parse_release(text)
    if args.dist not in (fields.get("Codename"), fields.get("Suite")):
        fail(f"{dists} is {fields.get('Codename')}, not {args.dist}")
    if args.arch not in fields.get("Architectures", "").split():
        fail(f"{args.dist} has no {args.arch}: it has {fields.get('Architectures')}")
    index = f"main/binary-{args.arch}/Packages.xz"
    if index not in digests:
        fail(f"the release gives no checksum for {index}")
    packages_xz = fetch(f"{dists}/{index}")
    check_digest(packages_xz, *digests[index], index)
    wanted = list(dict.fromkeys([*base_packages(gcc), *args.package]))
    found = parse_packages(lzma.decompress(packages_xz).decode("utf-8"), set(wanted))
    missing = [name for name in wanted if name not in found]
    if missing:
        fail(f"{args.dist}'s index for {args.arch} has no {', '.join(missing)}")

    log(f"Making {output}: Debian {fields.get('Version')} ({args.dist}), {args.arch}, from {mirror}")
    parent = os.path.dirname(output)
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f".{os.path.basename(output)}-", dir=parent)
    try:
        os.chmod(staging, 0o755)
        packages: list[dict[str, Any]] = []
        for name in wanted:
            stanza = found[name]
            deb = fetch(f"{mirror}/{stanza['Filename']}")
            check_digest(deb, stanza["SHA256"], int(stanza["Size"]), stanza["Filename"])
            with data_tar(deb, stanza["Filename"]) as tar:
                files = unpack(tar, staging)
            log(f"  {name} {stanza['Version']}: {files} files")
            packages.append(
                {
                    "name": name,
                    "version": stanza["Version"],
                    "filename": stanza["Filename"],
                    "sha256": stanza["SHA256"],
                }
            )
        remove_empty_directories(staging)
        manifest = {
            "v": 1,
            "arch": args.arch,
            "dist": args.dist,
            "version": fields.get("Version"),
            "date": fields.get("Date"),
            "mirror": mirror,
            "signatureChecked": keyring is not None,
            "packages": packages,
        }
        with open(os.path.join(staging, MANIFEST), "w") as f:
            json.dump(manifest, f, indent=1)
            f.write("\n")
        install(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log(f"Made {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
