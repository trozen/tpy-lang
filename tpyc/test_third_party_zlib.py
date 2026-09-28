"""Build-wiring unit tests for the vendored zlib managed dependency.

Toolchain-free: exercises the third-party registry and the direct-compile
build-plan resolution. The end-to-end compile + link + call is covered by the
`stdlib/zlib_basic` and `stdlib/gzip_basic` cases.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tpyc.build import third_party
from tpyc.cli import THIRD_PARTY_LIBS
from tpyc.build.third_party import (
    DisabledLibError,
    SystemLibVersionError,
    emit_cmake_snippet,
    get_lib,
    known_lib_names,
    resolve_build_plan,
)

RUNTIME_CPP = Path(__file__).resolve().parent.parent / "runtime" / "cpp"
ZLIB_DIR = RUNTIME_CPP / "third_party" / "zlib"


def _fake_pkgconfig(monkeypatch, version):
    def fake_run(cmd, *a, **k):
        if version is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return SimpleNamespace(returncode=0, stdout=version + "\n", stderr="")

    monkeypatch.setattr(third_party.subprocess, "run", fake_run)


def test_zlib_registered():
    assert "zlib" in known_lib_names()


def test_zlib_lib_declaration():
    lib = get_lib("zlib", RUNTIME_CPP)
    assert lib.cli_flag == "--zlib"
    assert lib.default_mode == "bundled"
    assert lib.system_link_flags == ("-lz",)
    assert lib.cmake_var == "TPY_ZLIB"
    assert lib.find_package_name == "ZLIB"
    assert lib.find_package_target == "ZLIB::ZLIB"
    assert lib.bundled_static_target == "zlibstatic"
    assert (lib.bundled_source_dir / lib.license_file).is_file()


def test_zlib_bundled_sources_and_flags():
    lib = get_lib("zlib", RUNTIME_CPP)
    names = {p.name for p in lib.bundled_source_files(lib)}
    assert {"inflate.c", "deflate.c", "crc32.c", "adler32.c", "zutil.c"} <= names
    # The gzFile API is not built (gzip is pure TPy), and the TPy shim is glue.
    assert not any(n.startswith("gz") for n in names)
    assert "zlib_shim.c" not in names
    assert lib.bundled_compile_flags(lib) == [f"-I{ZLIB_DIR}"]
    # Generated TUs include the facade, never zlib.h: no dependent -I.
    assert lib.bundled_user_include_dir is None
    assert ZLIB_DIR not in resolve_build_plan(["zlib"], RUNTIME_CPP, {}).extra_include_dirs


def test_zlib_cmake_shim_matches_manifest():
    lib = get_lib("zlib", RUNTIME_CPP)
    cmake = (ZLIB_DIR / "CMakeLists.txt").read_text()
    assert "add_library(zlibstatic STATIC" in cmake
    for src in lib.bundled_source_files(lib):
        assert src.name in cmake
    assert "ZLIB::ZLIB" in emit_cmake_snippet(lib)


def test_zlib_glue_in_every_mode(monkeypatch):
    bundled = resolve_build_plan(["zlib"], RUNTIME_CPP, {})
    names = [s.name for s, _ in bundled.c_sources]
    assert names.count("zlib_shim.c") == 1
    assert len(names) == len(set(names))
    shim_flags = next(f for s, f in bundled.c_sources if s.name == "zlib_shim.c")
    assert shim_flags == [f"-I{ZLIB_DIR}"]

    _fake_pkgconfig(monkeypatch, "1.3.1")
    system = resolve_build_plan(["zlib"], RUNTIME_CPP, {"zlib": "system"})
    assert [(s.name, f) for s, f in system.c_sources] == [("zlib_shim.c", [])]
    assert "-lz" in system.extra_link_flags


def test_zlib_system_too_old_raises(monkeypatch):
    _fake_pkgconfig(monkeypatch, "1.2.8")
    with pytest.raises(SystemLibVersionError):
        resolve_build_plan(["zlib"], RUNTIME_CPP, {"zlib": "system"})


def test_zlib_disabled_raises():
    with pytest.raises(DisabledLibError):
        resolve_build_plan(["zlib"], RUNTIME_CPP, {"zlib": "none"})


def test_cli_lists_every_managed_lib():
    assert sorted(THIRD_PARTY_LIBS) == known_lib_names()
