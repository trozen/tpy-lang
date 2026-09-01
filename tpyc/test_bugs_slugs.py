"""Keeps `BUGS.md` slug references resolvable.

A slug is only safe to cite from source because a dangling one fails here:
without this check a reference would silently outlive the entry it points at,
which is why bug references are otherwise banned from comments.
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# An entry's anchor: the bracketed slug directly after its bold headline.
ANCHOR_RE = re.compile(r"^- \*\*.+?\*\* \[`([^`]+)`\]")
# A citation, from anywhere including BUGS.md itself.
REFERENCE_RE = re.compile(r"BUGS\.md#([A-Za-z0-9_-]+)")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

SCANNED_SUFFIXES = {".py", ".md", ".hpp", ".cpp", ".h", ".c", ".txt"}
# Vendored sources and build/cache trees carry no TPy references.
PRUNED_DIRS = {".git", ".venv", "__pycache__", "__tpyc__", ".pytest_cache",
               ".cache", "node_modules", "third_party", "build", "dist"}


def _bugs_md() -> str:
    return (REPO_ROOT / "BUGS.md").read_text(encoding="utf-8")


def _anchors() -> list[str]:
    return [m.group(1) for line in _bugs_md().splitlines()
            if (m := ANCHOR_RE.match(line))]


def _scanned_files() -> list[Path]:
    """Walk rather than ask git: the remote test host has no repo metadata."""
    found: list[Path] = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNED_DIRS]
        for name in names:
            if Path(name).suffix in SCANNED_SUFFIXES:
                found.append(Path(root) / name)
    return found


def test_slugs_are_well_formed_and_unique():
    anchors = _anchors()
    bad = [s for s in anchors if not SLUG_RE.match(s)]
    assert not bad, f"slugs must be lowercase kebab-case: {bad}"
    dupes = sorted({s for s in anchors if anchors.count(s) > 1})
    assert not dupes, f"duplicate BUGS.md slugs: {dupes}"


def test_every_reference_resolves():
    anchors = set(_anchors())
    dangling: list[str] = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in REFERENCE_RE.finditer(text):
            slug = match.group(1)
            if slug not in anchors:
                line = text.count("\n", 0, match.start()) + 1
                rel = path.relative_to(REPO_ROOT)
                dangling.append(f"{rel}:{line}: BUGS.md#{slug}")
    assert not dangling, (
        "reference(s) to a BUGS.md slug that no entry defines -- if the entry "
        "was resolved and deleted, delete or reword the reference too:\n  "
        + "\n  ".join(dangling))
