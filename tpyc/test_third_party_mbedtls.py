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
    # Every library/*.c compiles standalone; the manifest pins the full set.
    assert len(srcs) > 100
    names = {p.name for p in srcs}
    assert {"version.c", "ssl_tls.c", "x509_crt.c"} <= names
    # The TPy shim and the compiled-in Mozilla CA bundle link with mbedTLS.
    assert {"mbedtls_shim.c", "cacert_data.c"} <= names
    assert all(p.suffix == ".c" and p.is_file() for p in srcs)

    flags = lib.bundled_compile_flags(lib)
    inc = lib.bundled_source_dir / "include"
    libdir = lib.bundled_source_dir / "library"
    assert f"-I{inc}" in flags
    assert f"-I{libdir}" in flags

    user_inc = lib.bundled_user_include_dir(lib)
    assert user_inc == inc
    assert (user_inc / "mbedtls" / "ssl.h").is_file()


def test_mbedtls_build_plan_bundled():
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {})
    assert len(plan.c_sources) > 100
    assert (RUNTIME_CPP / "third_party" / "mbedtls" / "include") in plan.extra_include_dirs


def test_mbedtls_build_plan_auto():
    # `auto` currently aliases to bundled in the direct-compile path (system
    # probing is a documented TODO); pin that so wiring up real probing later
    # is a deliberate, test-visible change rather than a silent one.
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "auto"})
    assert len(plan.c_sources) > 100
    assert (RUNTIME_CPP / "third_party" / "mbedtls" / "include") in plan.extra_include_dirs


def test_mbedtls_build_plan_system():
    plan = resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "system"})
    assert plan.c_sources == []
    for flag in ("-lmbedtls", "-lmbedx509", "-lmbedcrypto"):
        assert flag in plan.extra_link_flags


def test_mbedtls_disabled_raises():
    with pytest.raises(DisabledLibError):
        resolve_build_plan(["mbedtls"], RUNTIME_CPP, {"mbedtls": "none"})
