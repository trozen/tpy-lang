"""Packaging smoke: the built sdist must contain every lib/tpy file.

Guards against hatchling's sdist walk silently dropping a package: it follows
symlinks and dedups directories by inode, so a walk that reaches lib/cpy/tplib
(a symlink to lib/tpy/tplib) first can consume the inode and get the real
package pruned. pyproject.toml's `only-include` keeps lib/cpy out of the walk;
this test is what notices if that ever stops being true.
"""

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_sdist_contains_all_lib_tpy_files(tmp_path: Path) -> None:
    # Mirror hatchling's own selection semantics (a tree walk minus ignore
    # patterns) rather than asking git -- the tree may not be a checkout
    # (e.g. the rpytest offload host syncs without .git).
    ignored_dirs = {"__pycache__", "__tpyc__"}
    ignored_suffixes = {".pyc", ".pyo", ".gch"}
    lib_root = REPO_ROOT / "lib" / "tpy"
    expected = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in lib_root.rglob("*")
        if p.is_file()
        and not ignored_dirs.intersection(p.parts)
        and p.suffix not in ignored_suffixes
    )
    assert expected, f"no files found under {lib_root}"

    build = subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert build.returncode == 0, f"uv build --sdist failed:\n{build.stderr}"

    [sdist] = tmp_path.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        # Strip the leading "<name>-<version>/" component.
        shipped = {m.name.split("/", 1)[1] for m in tar.getmembers() if "/" in m.name}

    missing = sorted(set(expected) - shipped)
    assert not missing, f"sdist is missing lib/tpy files: {missing}"
