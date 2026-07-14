"""Build-wiring unit tests for the vendored Howard Hinnant date/tz dependency.

Toolchain-free: exercises the direct-compile build-plan resolution, not an
actual compile/link. Focus is the mode-independent glue (date_shim.cpp): our
facade shim is TPy-owned code, so it must compile and link in system mode too,
not only bundled -- only the vendored upstream sources are mode-gated.
"""

from pathlib import Path

from tpyc.build.third_party import get_lib, resolve_build_plan

RUNTIME_CPP = Path(__file__).resolve().parent.parent / "runtime" / "cpp"


def _glue_in(plan):
    return {s.name for s, _ in plan.c_sources if s.name == "date_shim.cpp"}


def test_date_glue_out_of_vendored_sources():
    lib = get_lib("date", RUNTIME_CPP)
    vendored = {p.name for p in lib.bundled_source_files(lib)}
    assert "date_shim.cpp" not in vendored
    assert {p.name for p in lib.glue_source_files(lib)} == {"date_shim.cpp"}


def test_date_glue_flags_per_mode():
    lib = get_lib("date", RUNTIME_CPP)
    runtime_inc = RUNTIME_CPP / "include"
    vendored_inc = lib.bundled_source_dir / "include"
    # The shim includes its TPy facade (runtime include, both modes) and
    # <date/tz.h> (vendored include bundled; default system paths in system).
    # USE_OS_TZDB governs date/tz.h behavior, so both modes must define it.
    bundled = lib.glue_compile_flags(lib, "bundled")
    assert f"-I{runtime_inc}" in bundled
    assert f"-I{vendored_inc}" in bundled
    assert "-DUSE_OS_TZDB=1" in bundled

    system = lib.glue_compile_flags(lib, "system")
    assert f"-I{runtime_inc}" in system
    assert f"-I{vendored_inc}" not in system
    assert "-DUSE_OS_TZDB=1" in system


def test_date_build_plan_bundled_compiles_glue_once():
    plan = resolve_build_plan(["date"], RUNTIME_CPP, {})
    assert _glue_in(plan) == {"date_shim.cpp"}
    names = [s.name for s, _ in plan.c_sources]
    assert len(names) == len(set(names))


def test_date_build_plan_system_still_compiles_glue():
    plan = resolve_build_plan(["date"], RUNTIME_CPP, {"date": "system"})
    # Vendored tz.cpp is gated out, but the shim still compiles and links.
    assert _glue_in(plan) == {"date_shim.cpp"}
    assert "-ldate-tz" in plan.extra_link_flags
