"""Packaging smoke: the built sdist must contain every tracked lib/tpy file.

Guards against hatchling's sdist walk silently dropping a package: it follows
symlinks and dedups directories by inode, so lib/cpy/tplib (a symlink to
lib/tpy/tplib) can consume the inode and get the real package pruned -- see
the force-include note in pyproject.toml.
"""

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_sdist_contains_all_tracked_lib_tpy_files(tmp_path: Path) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "lib/tpy"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert tracked, "git ls-files returned nothing -- not a git checkout?"

    build = subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert build.returncode == 0, f"uv build --sdist failed:\n{build.stderr}"

    [sdist] = tmp_path.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        # Strip the leading "<name>-<version>/" component.
        shipped = {m.name.split("/", 1)[1] for m in tar.getmembers() if "/" in m.name}

    missing = sorted(set(tracked) - shipped)
    assert not missing, f"sdist is missing tracked lib/tpy files: {missing}"
