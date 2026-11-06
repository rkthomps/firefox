#!/usr/bin/env python3
"""Tests of sysroot.py, on a small fake Debian archive.

  python3 phoenix-tools/tests/test-sysroot.py

Each run writes an archive whose one release, bookworm, lists the packages sysroot.py takes for
armhf, signs the release with a key made for the run, and makes sysroots from it through a
file:// URL. It needs gpg.
"""

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
from typing import Any

TESTS = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(TESTS), "sysroot.py")
PACKAGES = (
    "libc6",
    "libc6-dev",
    "linux-libc-dev",
    "libgcc-s1",
    "libgcc-12-dev",
    "libstdc++6",
    "libstdc++-12-dev",
    "zlib1g",
    "zlib1g-dev",
)
# Members of the packages' data.tar, beyond each package's usr/include/<name>.h and
# usr/share/doc/<name>/copyright: a path ending in / is a directory, content starting with "-> "
# a symlink's target.
EXTRA_MEMBERS: dict[str, list[tuple[str, str]]] = {
    "libc6": [
        ("lib/", ""),
        ("lib/arm-linux-gnueabihf/", ""),
        ("lib/arm-linux-gnueabihf/ld-linux-armhf.so.3", "loader"),
        ("lib/arm-linux-gnueabihf/libm.so.6", "libm"),
        ("lib/ld-linux-armhf.so.3", "-> /lib/arm-linux-gnueabihf/ld-linux-armhf.so.3"),
    ],
    "libc6-dev": [
        ("usr/lib/arm-linux-gnueabihf/libm.so", "-> /lib/arm-linux-gnueabihf/libm.so.6"),
        ("usr/lib/arm-linux-gnueabihf/libc_nonshared.a", "archive"),
        ("usr/lib/arm-linux-gnueabihf/libc.a", "-> libc_nonshared.a"),
        ("usr/lib/debug/", ""),
        ("usr/lib/debug/libc.debug", "debug info"),
        ("usr/bin/ldd", "a program"),
    ],
}
RELEASE = """\
Origin: Debian
Suite: stable
Version: 12.0
Codename: bookworm
Date: Sat, 10 Jun 2023 08:51:17 UTC
Architectures: amd64 armhf
Components: main
SHA256:
 {digest} {size} main/binary-armhf/Packages.xz
"""

checks = 0
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(message)


def tar_xz(members: list[tuple[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        for path, content in members:
            info = tarfile.TarInfo(f"./{path}")
            if path.endswith("/"):
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif content.startswith("-> "):
                info.type = tarfile.SYMTYPE
                info.linkname = content[3:]
                tar.addfile(info)
            else:
                data = content.encode()
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def ar(members: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        out += f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n".encode()
        out += data
        if len(data) % 2:
            out += b"\n"
    return bytes(out)


def gpg(home: str, *args: str) -> None:
    subprocess.run(
        ["gpg", "--homedir", home, "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
         *args],
        check=True,
        capture_output=True,
    )


def make_archive(root: str, home: str) -> tuple[str, str]:
    """Writes the archive and a key, in `root`. Returns the archive's URL and the keyring."""
    archive = os.path.join(root, "archive")
    stanzas: list[str] = []
    for name in PACKAGES:
        members = [
            (f"usr/include/{name}.h", f"// {name}\n"),
            (f"usr/share/doc/{name}/copyright", "a licence"),
            *EXTRA_MEMBERS.get(name, []),
        ]
        deb = ar(
            [
                ("debian-binary", b"2.0\n"),
                ("control.tar.xz", tar_xz([])),
                ("data.tar.xz", tar_xz(members)),
            ]
        )
        filename = f"pool/main/{name}_1.0-1_armhf.deb"
        os.makedirs(os.path.join(archive, "pool", "main"), exist_ok=True)
        with open(os.path.join(archive, filename), "wb") as f:
            f.write(deb)
        stanzas.append(
            f"Package: {name}\nVersion: 1.0-1\nArchitecture: armhf\nFilename: {filename}\n"
            f"Size: {len(deb)}\nSHA256: {hashlib.sha256(deb).hexdigest()}\n"
        )
    index = lzma.compress("\n".join(stanzas).encode())
    dists = os.path.join(archive, "dists", "bookworm")
    os.makedirs(os.path.join(dists, "main", "binary-armhf"))
    with open(os.path.join(dists, "main", "binary-armhf", "Packages.xz"), "wb") as f:
        f.write(index)
    with open(os.path.join(dists, "Release"), "w") as f:
        f.write(RELEASE.format(digest=hashlib.sha256(index).hexdigest(), size=len(index)))

    gpg(home, "--quick-gen-key", "Phoenix test <test@example.invalid>", "ed25519", "sign", "never")
    keyring = os.path.join(root, "keyring.gpg")
    gpg(home, "--output", keyring, "--export")
    gpg(home, "--output", os.path.join(dists, "InRelease"), "--clearsign",
        os.path.join(dists, "Release"))
    return f"file://{archive}", keyring


def run(mirror: str, output: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, TOOL, "--arch", "armhf", "--mirror", mirror, "-o", output, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def manifest_of(sysroot: str) -> dict[str, Any]:
    with open(os.path.join(sysroot, "phoenix-sysroot.json")) as f:
        return json.load(f)


def test_unsigned(mirror: str, root: str) -> None:
    sysroot = os.path.join(root, "unsigned")
    result = run(mirror, sysroot, "--no-signature-check")
    check(result.returncode == 0, f"making a sysroot failed:\n{result.stderr}")
    if result.returncode != 0:
        return
    for name in PACKAGES:
        check(
            os.path.isfile(os.path.join(sysroot, "usr", "include", f"{name}.h")),
            f"{name}'s header wasn't unpacked",
        )
    for dropped in ("usr/share", "usr/bin", "usr/lib/debug"):
        check(not os.path.lexists(os.path.join(sysroot, dropped)), f"{dropped} was unpacked")
    links = {
        "lib/ld-linux-armhf.so.3": "arm-linux-gnueabihf/ld-linux-armhf.so.3",
        "usr/lib/arm-linux-gnueabihf/libm.so": "../../../lib/arm-linux-gnueabihf/libm.so.6",
        "usr/lib/arm-linux-gnueabihf/libc.a": "libc_nonshared.a",
    }
    for link, target in links.items():
        path = os.path.join(sysroot, link)
        check(
            os.path.islink(path) and os.readlink(path) == target,
            f"{link} isn't a symlink to {target}",
        )
        check(
            os.path.realpath(path).startswith(os.path.realpath(sysroot) + os.sep)
            and os.path.isfile(path),
            f"{link} doesn't resolve to a file of the sysroot",
        )
    manifest = manifest_of(sysroot)
    check(manifest["v"] == 1 and manifest["version"] == "12.0", f"bad manifest: {manifest}")
    check(manifest["signatureChecked"] is False, "an unchecked signature counts as checked")
    check(
        [p["name"] for p in manifest["packages"]] == list(PACKAGES),
        f"the manifest's packages are {manifest['packages']}",
    )

    result = run(mirror, sysroot, "--no-signature-check")
    check(
        result.returncode != 0 and "exists" in result.stderr,
        f"an existing sysroot was replaced without --force:\n{result.stderr}",
    )
    result = run(mirror, sysroot, "--no-signature-check", "--force")
    check(result.returncode == 0, f"--force didn't replace the sysroot:\n{result.stderr}")

    foreign = os.path.join(root, "foreign")
    os.makedirs(foreign)
    result = run(mirror, foreign, "--no-signature-check", "--force")
    check(
        result.returncode != 0 and "didn't make it" in result.stderr,
        f"--force replaced a directory the script didn't make:\n{result.stderr}",
    )


def test_signed(mirror: str, root: str, keyring: str) -> None:
    sysroot = os.path.join(root, "signed")
    result = run(mirror, sysroot, "--keyring", keyring)
    check(result.returncode == 0, f"making a sysroot with a signature failed:\n{result.stderr}")
    if result.returncode == 0:
        check(manifest_of(sysroot)["signatureChecked"] is True, "a checked signature isn't noted")


def test_bad_signature(mirror: str, root: str, keyring: str) -> None:
    path = os.path.join(mirror.removeprefix("file://"), "dists", "bookworm", "InRelease")
    with open(path) as f:
        signed = f.read()
    with open(path, "w") as f:
        f.write(signed.replace("Version: 12.0", "Version: 12.9"))
    sysroot = os.path.join(root, "bad-signature")
    result = run(mirror, sysroot, "--keyring", keyring)
    check(
        result.returncode != 0 and "signature" in result.stderr,
        f"a changed InRelease passed the signature check:\n{result.stderr}",
    )
    check(not os.path.lexists(sysroot), "a sysroot was made despite a bad signature")
    with open(path, "w") as f:
        f.write(signed)


def test_bad_checksum(mirror: str, root: str) -> None:
    path = os.path.join(mirror.removeprefix("file://"), "pool", "main", "libc6_1.0-1_armhf.deb")
    with open(path, "rb") as f:
        deb = f.read()
    with open(path, "wb") as f:
        f.write(deb + b"\n")
    sysroot = os.path.join(root, "bad-checksum")
    result = run(mirror, sysroot, "--no-signature-check")
    check(
        result.returncode != 0 and "checksum" in result.stderr,
        f"a changed package passed the checksum:\n{result.stderr}",
    )
    check(not os.path.lexists(sysroot), "a sysroot was made despite a bad checksum")
    leftovers = [name for name in os.listdir(root) if name.startswith(".bad-checksum")]
    check(not leftovers, f"a failed run left {leftovers}")
    with open(path, "wb") as f:
        f.write(deb)


def test_missing_package(mirror: str, root: str) -> None:
    result = run(
        mirror, os.path.join(root, "missing"), "--no-signature-check", "--package", "libnothing"
    )
    check(
        result.returncode != 0 and "libnothing" in result.stderr,
        f"a package missing from the index wasn't reported:\n{result.stderr}",
    )


def main() -> int:
    if shutil.which("gpg") is None:
        print("gpg isn't installed")
        return 1
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
        try:
            mirror, keyring = make_archive(root, home)
            test_unsigned(mirror, root)
            test_signed(mirror, root, keyring)
            test_bad_signature(mirror, root, keyring)
            test_bad_checksum(mirror, root)
            test_missing_package(mirror, root)
        finally:
            subprocess.run(
                ["gpgconf", "--homedir", home, "--kill", "gpg-agent"],
                capture_output=True,
                check=False,
            )
    if failures:
        print(f"{len(failures)} of {checks} checks failed:")
        for message in failures:
            print(f"- {message}")
        return 1
    print(f"{checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
