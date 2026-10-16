"""Packaging smoke: the built sdist must contain every lib/tpy and lib/cpy/tpy
file, and the wheel must ship the CPython-side `tpy` and `tplib` packages.

Guards against hatchling's sdist walk silently dropping a package: it follows
symlinks and dedups directories by inode, so a walk that reaches lib/cpy/tplib
(a symlink to lib/tpy/tplib) first can consume the inode and get the real
package pruned. pyproject.toml's `only-include` keeps that symlink out of the
walk; this test is what notices if that ever stops being true.
"""

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _shippable(root: Path) -> list[Path]:
    # Mirror hatchling's own selection semantics (a tree walk minus ignore
    # patterns) rather than asking git -- the tree may not be a checkout
    # (e.g. the rpytest offload host syncs without .git).
    ignored_dirs = {"__pycache__", "__tpyc__"}
    ignored_suffixes = {".pyc", ".pyo", ".gch"}
    return [p for p in root.rglob("*")
            if p.is_file() and not ignored_dirs.intersection(p.parts)
            and p.suffix not in ignored_suffixes]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_sdist_contains_all_lib_tpy_files(tmp_path: Path) -> None:
    expected = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for root in (REPO_ROOT / "lib" / "tpy", REPO_ROOT / "lib" / "cpy" / "tpy")
        for p in _shippable(root))
    assert expected, "no files found under lib/tpy"

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
    assert not missing, f"sdist is missing lib files: {missing}"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_wheel_ships_the_cpython_packages(tmp_path: Path) -> None:
    # `tplib` is the real lib/tpy/tplib (lib/cpy/tplib only links to it).
    expected = sorted(
        [("tpy/" + p.relative_to(REPO_ROOT / "lib" / "cpy" / "tpy").as_posix())
         for p in _shippable(REPO_ROOT / "lib" / "cpy" / "tpy")]
        + [("tplib/" + p.relative_to(REPO_ROOT / "lib" / "tpy" / "tplib").as_posix())
           for p in _shippable(REPO_ROOT / "lib" / "tpy" / "tplib")])

    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert build.returncode == 0, f"uv build --wheel failed:\n{build.stderr}"

    [wheel] = tmp_path.glob("tpy_lang-*.whl")
    with zipfile.ZipFile(wheel) as whl:
        names = whl.namelist()
    assert sorted(n for n in names if n.startswith(("tpy/", "tplib/"))) == expected
    tops = {n.split("/", 1)[0] for n in names if not n.split("/", 1)[0].endswith(".dist-info")}
    assert tops == {"tpyc", "tpy", "tplib"}
