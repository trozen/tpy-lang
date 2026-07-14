"""Build-wiring unit tests for the vendored mbedTLS managed dependency.

Toolchain-free: exercises the third-party registry and the direct-compile
build-plan resolution, not an actual compile/link. The end-to-end
compile+link+call is exercised once the public `ssl` module imports the
bindings (that public consumer is what pulls mbedTLS into the linked
stdlib set, the same way `re` pulls in `_bindings.pcre2`).
"""

from pathlib import Path

import pytest

from tpyc.build.third_party import (
    DisabledLibError,
    get_lib,
    known_lib_names,
    resolve_build_plan,
)

RUNTIME_CPP = Path(__file__).resolve().parent.parent / "runtime" / "cpp"


def test_mbedtls_registered():
    assert "mbedtls" in known_lib_names()


def test_mbedtls_lib_declaration():
    lib = get_lib("mbedtls", RUNTIME_CPP)
    assert lib.cli_flag == "--mbedtls"
    assert lib.default_mode == "bundled"
    # tls -> x509 -> crypto link order for the system path.
    assert lib.system_link_flags == ("-lmbedtls", "-lmbedx509", "-lmbedcrypto")
    # CMake-emit identity: a wrong package name / target would only surface
    # at `tpyc -o` configure time, which no other test exercises.
    assert lib.cmake_var == "TPY_MBEDTLS"
    assert lib.find_package_name == "MbedTLS"
    assert lib.find_package_target == "MbedTLS::mbedtls"


def test_mbedtls_bundled_sources_and_flags():
    lib = get_lib("mbedtls", RUNTIME_CPP)
    srcs = lib.bundled_source_files(lib)
    # bundled_source_files is the VENDORED upstream set only -- every library/*.c
    # compiles standalone; the manifest pins the full set. TPy-owned glue is not
    # here (it is mode-independent; see the glue tests below).
    assert len(srcs) > 100
    names = {p.name for p in srcs}
    assert {"version.c", "ssl_tls.c", "x509_crt.c"} <= names
    assert not ({"mbedtls_shim.c", "cacert_data.c"} & names)
    assert all(p.suffix == ".c" and p.is_file() for p in srcs)

    flags = lib.bundled_compile_flags(lib)
    inc = lib.bundled_source_dir / "include"
    libdir = lib.bundled_source_dir / "library"
    assert f"-I{inc}" in flags
    assert f"-I{libdir}" in flags

    user_inc = lib.bundled_user_include_dir(lib)
    assert user_inc == inc
    assert (user_inc / "mbedtls" / "ssl.h").is_file()


def test_mbedtls_glue_sources_and_flags():
    lib = get_lib("mbedtls", RUNTIME_CPP)
    glue = {p.name: p for p in lib.glue_source_files(lib)}
    # The ssl FFI shim (defines tpy_tls_*) + the compiled-in Mozilla CA bundle.
    assert set(glue) == {"mbedtls_shim.c", "cacert_data.c"}
    assert all(p.is_file() for p in glue.values())

    inc = lib.bundled_source_dir / "include"
    # Bundled: the shim's <mbedtls/*.h> resolve against the vendored include.
    assert f"-I{inc}" in lib.glue_compile_flags(lib, "bundled")
    # System: no -I; the shim resolves headers via default system search paths.
    assert lib.glue_compile_flags(lib, "system") == []


def _glue_in(plan):
    return {s.name for s, _ in plan.c_sources if s.name in ("mbedtls_shim.c", "cacert_data.c")}


def test_mbedtls_build_plan_bundled():
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {})
    assert len(plan.c_sources) > 100
    # Glue compiles in bundled mode too, exactly once (no double-compile with
    # the vendored set) with the vendored include on its flags.
    assert _glue_in(plan) == {"mbedtls_shim.c", "cacert_data.c"}
    names = [s.name for s, _ in plan.c_sources]
    assert len(names) == len(set(names))
    inc = RUNTIME_CPP / "third_party" / "mbedtls" / "include"
    shim_flags = next(f for s, f in plan.c_sources if s.name == "mbedtls_shim.c")
    assert f"-I{inc}" in shim_flags
    assert inc in plan.extra_include_dirs


def test_mbedtls_build_plan_auto():
    # `auto` currently aliases to bundled in the direct-compile path (system
    # probing is a documented TODO); pin that so wiring up real probing later
    # is a deliberate, test-visible change rather than a silent one.
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "auto"})
    assert len(plan.c_sources) > 100
    assert (RUNTIME_CPP / "third_party" / "mbedtls" / "include") in plan.extra_include_dirs


def test_mbedtls_build_plan_system(monkeypatch):
    # Pin a compatible system version so this exercises glue wiring, not the
    # version guard, regardless of what mbedTLS the host has installed.
    _fake_pkgconfig(monkeypatch, "3.6.6")
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "system"})
    # System mode gates out the vendored upstream sources but still compiles
    # the TPy-owned glue -- it is our FFI surface, so omitting it leaves
    # tpystd::ssl with undefined references to tpy_tls_*. Links the system
    # archives, not the vendored .a.
    assert _glue_in(plan) == {"mbedtls_shim.c", "cacert_data.c"}
    assert len(plan.c_sources) == 2
    shim_flags = next(f for s, f in plan.c_sources if s.name == "mbedtls_shim.c")
    assert shim_flags == []  # system headers via default search paths
    for flag in ("-lmbedtls", "-lmbedx509", "-lmbedcrypto"):
        assert flag in plan.extra_link_flags


def test_mbedtls_disabled_raises():
    with pytest.raises(DisabledLibError):
        resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "none"})


def _fake_pkgconfig(monkeypatch, version):
    """Force pkg-config --modversion to report `version` (or fail if None)."""
    from types import SimpleNamespace
    from tpyc.build import third_party

    def fake_run(cmd, *a, **k):
        if version is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return SimpleNamespace(returncode=0, stdout=version + "\n", stderr="")

    monkeypatch.setattr(third_party.subprocess, "run", fake_run)


def test_mbedtls_system_too_old_raises(monkeypatch):
    from tpyc.build.third_party import SystemLibVersionError
    _fake_pkgconfig(monkeypatch, "2.27.0")  # < declared min (2.28.0)
    with pytest.raises(SystemLibVersionError) as ei:
        resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "system"})
    assert "2.27.0" in str(ei.value) and "2.28.0" in str(ei.value)


def test_mbedtls_system_new_enough_ok(monkeypatch):
    # Both the 2.28 LTS branch (distro default) and 3.x are supported.
    for ver in ("2.28.8", "3.6.6"):
        _fake_pkgconfig(monkeypatch, ver)
        plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "system"})
        assert {s.name for s, _ in plan.c_sources} == {"mbedtls_shim.c", "cacert_data.c"}


def test_mbedtls_system_short_form_version_ok(monkeypatch):
    # A short-form report ("2.28") equals the declared min "2.28.0" -- it must
    # NOT be read as older by the tuple compare (regression: (2,28) < (2,28,0)).
    _fake_pkgconfig(monkeypatch, "2.28")
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "system"})
    assert {s.name for s, _ in plan.c_sources} == {"mbedtls_shim.c", "cacert_data.c"}


def test_mbedtls_system_unknown_version_is_permissive(monkeypatch):
    # pkg-config absent / no .pc: version undeterminable -> don't block (a
    # from-source install may still be fine).
    _fake_pkgconfig(monkeypatch, None)
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "system"})
    assert {s.name for s, _ in plan.c_sources} == {"mbedtls_shim.c", "cacert_data.c"}
