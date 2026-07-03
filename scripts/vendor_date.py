#!/usr/bin/env python3
"""Vendor Howard Hinnant's date library into runtime/cpp/third_party/date/.

Idempotent: if the target tree already matches the pinned version (per the
sidecar date.vendor.json), this script is a no-op.

Usage:
    uv run python scripts/vendor_date.py          # download + extract + strip
    uv run python scripts/vendor_date.py --force  # re-vendor even if marker matches

To bump date:
    1. Update VERSION + SHA256 + URL below
    2. Run this script (without --force)
    3. Review the diff under runtime/cpp/third_party/date/
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

# ---- Pinned date release ----
NAME = "date"
VERSION = "3.0.4"
URL = (
    "https://github.com/HowardHinnant/date/archive/refs/tags/"
    f"v{VERSION}.tar.gz"
)
SHA256 = "56e05531ee8994124eeb498d0e6a5e1c3b9d4fccbecdf555fe266631368fb55f"

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
# The test suite dominates the tarball's size; CI configs are irrelevant to
# a vendored build.
STRIP_DIRS: tuple[str, ...] = (
    "test",
    "ci",
)

STRIP_FILES: tuple[str, ...] = (
    ".gitattributes",
    ".gitignore",
    ".travis.yml",
    "compile_fail.sh",
    "test_fail.sh",
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
    """Extract tarball into dest, stripping the top-level date-VERSION/ prefix."""
    print(f"  extracting -> {dest}", file=sys.stderr)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tarball, "r:gz") as tar:
        prefix = f"date-{VERSION}/"
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
    print("  stripping unused files/dirs", file=sys.stderr)
    for d in STRIP_DIRS:
        path = dest / d
        if path.is_dir():
            shutil.rmtree(path)
    for f in STRIP_FILES:
        path = dest / f
        if path.is_file():
            path.unlink()


def _stamp(metadata: dict) -> str:
    """Timestamp for `vendored_at`: reuse the prior one when every substantive
    field is unchanged, so a re-run producing identical content (e.g. --force)
    is a true no-op instead of churning the timestamp."""
    prior = _read_sidecar()
    if prior is not None and "vendored_at" in prior \
            and all(prior.get(k) == v for k, v in metadata.items()):
        return prior["vendored_at"]
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _write_sidecar() -> None:
    """Record what we just vendored, next to (not inside) the source tree."""
    metadata = {
        "name": NAME,
        "version": VERSION,
        "url": URL,
        "sha256": SHA256,
    }
    metadata["vendored_at"] = _stamp(metadata)
    SIDECAR.write_text(json.dumps(metadata, indent=2) + "\n")


def _read_sidecar() -> dict | None:
    """Parse the sidecar JSON, or None if missing/invalid."""
    if not SIDECAR.is_file():
        return None
    try:
        return json.loads(SIDECAR.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _read_sidecar_version() -> str | None:
    """Return the version recorded in the sidecar, or None if missing/invalid."""
    prior = _read_sidecar()
    return prior.get("version") if prior is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--force", action="store_true",
        help="re-vendor even if the version marker already matches",
    )
    args = parser.parse_args()

    current = _read_sidecar_version()
    if not args.force and current == VERSION:
        print(f"date {VERSION} already vendored "
              f"(per {SIDECAR.relative_to(REPO_ROOT)})", file=sys.stderr)
        print("Pass --force to re-vendor.", file=sys.stderr)
        return 0
    if current and current != VERSION:
        print(f"Sidecar says date {current}, want {VERSION} -- re-vendoring",
              file=sys.stderr)

    print(f"Vendoring date {VERSION}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="vendor_date_") as tmp:
        tarball = Path(tmp) / f"date-{VERSION}.tar.gz"
        _download(URL, tarball)
        actual = _sha256(tarball)
        if actual != SHA256:
            print("ERROR: SHA256 mismatch", file=sys.stderr)
            print(f"  expected: {SHA256}", file=sys.stderr)
            print(f"  actual:   {actual}", file=sys.stderr)
            return 1
        print(f"  sha256 verified: {actual}", file=sys.stderr)
        _extract(tarball, DEST_DIR)

    _strip_unused(DEST_DIR)
    _write_sidecar()

    file_count = sum(1 for _ in DEST_DIR.rglob("*") if _.is_file())
    size_mb = sum(p.stat().st_size for p in DEST_DIR.rglob("*") if p.is_file()) / (1024 * 1024)
    print(f"Done. {file_count} files, {size_mb:.1f} MiB at {DEST_DIR}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
