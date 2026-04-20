#!/usr/bin/env python3
"""Vendor PCRE2 source into runtime/cpp/third_party/pcre2/.

Idempotent: if the target tree already matches the pinned version (per the
VERSION marker file), this script is a no-op.

Usage:
    uv run python scripts/vendor_pcre2.py        # download + extract + strip
    uv run python scripts/vendor_pcre2.py --force  # re-vendor even if marker matches

To bump PCRE2:
    1. Update VERSION + SHA256 + URL below
    2. Run this script (without --force)
    3. Review the diff under runtime/cpp/third_party/pcre2/
    4. Commit
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# ---- Pinned PCRE2 release ----
NAME = "pcre2"
VERSION = "10.44"
URL = (
    "https://github.com/PCRE2Project/pcre2/releases/download/"
    f"pcre2-{VERSION}/pcre2-{VERSION}.tar.gz"
)
SHA256 = "86b9cb0aa3bcb7994faa88018292bc704cdbb708e785f7c74352ff6ea7d3175b"

# ---- Vendoring layout ----
# Sidecar metadata lives NEXT TO the vendored tree, not inside it -- the
# upstream source tree stays pristine, no risk of colliding with a future
# upstream file of the same name.
REPO_ROOT = Path(__file__).resolve().parent.parent
THIRD_PARTY_DIR = REPO_ROOT / "runtime" / "cpp" / "third_party"
DEST_DIR = THIRD_PARTY_DIR / NAME
SIDECAR = THIRD_PARTY_DIR / f"{NAME}.vendor.json"

# ---- Strip rules ----
# Whole directories to remove from the extracted tree (relative to DEST_DIR).
# We don't ship docs (man pages, html), test inputs/outputs, autotools
# macros, or VMS-specific build files.
STRIP_DIRS: tuple[str, ...] = (
    "doc",
    "testdata",
    "m4",
    "vms",
    "autom4te.cache",
)

# Individual files to remove. Mostly autotools detritus + release-tooling
# helpers that are irrelevant for a vendored CMake-only build path.
STRIP_FILES: tuple[str, ...] = (
    "aclocal.m4",
    "ar-lib",
    "compile",
    "config.guess",
    "config.sub",
    "configure",
    "configure.ac",
    "depcomp",
    "install-sh",
    "ltmain.sh",
    "missing",
    "test-driver",
    "Makefile.am",
    "Makefile.in",
    "config.h.in.in",
    # release-management scripts
    "132html",
    "CheckMan",
    "CleanTxt",
    "Detrail",
    "PrepareRelease",
    "perltest.sh",
    "RunGrepTest",
    "RunGrepTest.bat",
    "RunTest",
    "RunTest.bat",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as resp:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def _extract(tarball: Path, dest: Path) -> None:
    """Extract tarball into dest, stripping the top-level pcre2-VERSION/ prefix."""
    print(f"  extracting -> {dest}", file=sys.stderr)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tarball, "r:gz") as tar:
        prefix = f"pcre2-{VERSION}/"
        members = []
        for m in tar.getmembers():
            if not m.name.startswith(prefix):
                continue
            stripped = m.name[len(prefix):]
            if not stripped:
                continue  # the top-level dir entry itself
            m.name = stripped
            members.append(m)
        # filter='data' (Python 3.12+) is the safe default; fall back if older
        try:
            tar.extractall(dest, members=members, filter="data")
        except TypeError:
            tar.extractall(dest, members=members)


def _strip_unused(dest: Path) -> None:
    print(f"  stripping unused files/dirs", file=sys.stderr)
    for d in STRIP_DIRS:
        path = dest / d
        if path.is_dir():
            shutil.rmtree(path)
    for f in STRIP_FILES:
        path = dest / f
        if path.is_file():
            path.unlink()


def _materialize_headers(dest: Path) -> None:
    """Activate PCRE2's non-autotools build path: copy .generic / .dist
    templates to their working names, then enable our build options in
    config.h.

    See dest/NON-AUTOTOOLS-BUILD for upstream documentation of this flow.
    """
    print(f"  materializing pcre2.h, config.h, pcre2_chartables.c", file=sys.stderr)
    src = dest / "src"
    shutil.copy2(src / "pcre2.h.generic", src / "pcre2.h")
    shutil.copy2(src / "pcre2_chartables.c.dist", src / "pcre2_chartables.c")

    # config.h.generic comes with all SUPPORT_* macros undefined. We enable
    # 8-bit code units (matches our CODE_UNIT_WIDTH=8 build), JIT, and
    # Unicode property support.
    text = (src / "config.h.generic").read_text()
    text = text.replace(
        "/* #undef SUPPORT_PCRE2_8 */", "#define SUPPORT_PCRE2_8 1")
    text = text.replace(
        "/* #undef SUPPORT_JIT */", "#define SUPPORT_JIT 1")
    text = text.replace(
        "/* #undef SUPPORT_UNICODE */", "#define SUPPORT_UNICODE 1")
    (src / "config.h").write_text(text)


def _write_sidecar() -> None:
    """Record what we just vendored, next to (not inside) the source tree."""
    metadata = {
        "name": NAME,
        "version": VERSION,
        "url": URL,
        "sha256": SHA256,
        "vendored_at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"),
    }
    SIDECAR.write_text(json.dumps(metadata, indent=2) + "\n")


def _read_sidecar_version() -> str | None:
    """Return the version recorded in the sidecar, or None if missing/invalid."""
    if not SIDECAR.is_file():
        return None
    try:
        return json.loads(SIDECAR.read_text()).get("version")
    except (json.JSONDecodeError, OSError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--force", action="store_true",
        help="re-vendor even if the version marker already matches",
    )
    args = parser.parse_args()

    current = _read_sidecar_version()
    if not args.force and current == VERSION:
        print(f"PCRE2 {VERSION} already vendored "
              f"(per {SIDECAR.relative_to(REPO_ROOT)})", file=sys.stderr)
        print("Pass --force to re-vendor.", file=sys.stderr)
        return 0
    if current and current != VERSION:
        print(f"Sidecar says PCRE2 {current}, want {VERSION} -- re-vendoring",
              file=sys.stderr)

    print(f"Vendoring PCRE2 {VERSION}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="vendor_pcre2_") as tmp:
        tarball = Path(tmp) / f"pcre2-{VERSION}.tar.gz"
        _download(URL, tarball)
        actual = _sha256(tarball)
        if actual != SHA256:
            print(f"ERROR: SHA256 mismatch", file=sys.stderr)
            print(f"  expected: {SHA256}", file=sys.stderr)
            print(f"  actual:   {actual}", file=sys.stderr)
            return 1
        print(f"  sha256 verified: {actual}", file=sys.stderr)
        _extract(tarball, DEST_DIR)

    _strip_unused(DEST_DIR)
    _materialize_headers(DEST_DIR)
    _write_sidecar()

    file_count = sum(1 for _ in DEST_DIR.rglob("*") if _.is_file())
    size_mb = sum(p.stat().st_size for p in DEST_DIR.rglob("*") if p.is_file()) / (1024 * 1024)
    print(f"Done. {file_count} files, {size_mb:.1f} MiB at {DEST_DIR}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
