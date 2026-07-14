"""Howard Hinnant date-specific build wiring. Registered in
``tpyc/build/third_party.py::_FACTORIES``.

The vendored tree provides the tz backend for the ``datetime`` stdlib
module: one C++ source (``src/tz.cpp``) built with ``USE_OS_TZDB`` so it
parses the OS zoneinfo database directly -- in-memory lookups, no
``localtime_r`` per-call global lock, no network fetch. The direct-compile
path here:

  * enumerates the .cpp files (from the sidecar manifest
    ``runtime/cpp/third_party/date.sources.txt``) plus the TPy-owned
    facade shim ``runtime/cpp/src/stdlib/date_shim.cpp``;
  * emits the -I / -D flags they need;
  * exposes no user include dir -- generated TUs talk only to the
    hand-written facade ``tpy/stdlib/datetime.hpp``, never to the
    upstream headers.
"""

from __future__ import annotations

from pathlib import Path

from .third_party import (
    ThirdPartyLib, ThirdPartyMode, read_source_manifest, require_bundled_dir,
)


_SOURCE_MANIFEST = "date.sources.txt"


def _source_files(lib: ThirdPartyLib) -> list[Path]:
    """The C++ files to compile into the bundled tz backend: upstream
    tz.cpp (per the manifest) plus the TPy-owned facade shim. The shim is
    compiled here, with the vendored sources, so it links only when a
    module declares the dep and gets the same include/define flags -- not
    via discover_runtime_cpp_sources (always-on, which would drag the
    vendored tree into every binary)."""
    bd = require_bundled_dir(lib)
    src_dir = bd / "src"
    if not src_dir.is_dir():
        raise FileNotFoundError(
            f"{lib.name}: bundled src/ not found at {src_dir}"
        )
    manifest = bd.parent / _SOURCE_MANIFEST
    if not manifest.is_file():
        raise FileNotFoundError(
            f"{lib.name}: source manifest not found at {manifest}"
        )
    paths: list[Path] = []
    for name in read_source_manifest(manifest):
        p = src_dir / name
        if not p.is_file():
            raise FileNotFoundError(
                f"{lib.name}: expected source file missing: {p} "
                f"(listed in {manifest})"
            )
        paths.append(p)
    return paths


def _glue_source_files(lib: ThirdPartyLib) -> list[Path]:
    """TPy-owned glue (mode-independent): the datetime module's facade shim. Our
    code, so it must link in system mode too -- not routed via
    discover_runtime_cpp_sources (always-on), which would drag it into every
    binary rather than only those that declare the dep."""
    runtime_cpp = require_bundled_dir(lib).parent.parent
    shim = runtime_cpp / "src" / "stdlib" / "date_shim.cpp"
    if not shim.is_file():
        raise FileNotFoundError(f"{lib.name}: shim not found at {shim}")
    return [shim]


def _tz_defines() -> list[str]:
    """USE_OS_TZDB parses the OS zoneinfo tree (Linux/macOS; no bundled tzdata,
    no remote API). These govern date/tz.h behavior, so every TU that includes
    it -- vendored sources and the shim, in either mode -- must define them."""
    return ["-DUSE_OS_TZDB=1", "-DHAS_REMOTE_API=0"]


def _compile_flags(lib: ThirdPartyLib) -> list[str]:
    """C++-compiler flags for the bundled tz backend sources. The runtime
    include dir lets the shim include its own facade header so signature drift
    is a compile error."""
    bd = require_bundled_dir(lib)
    runtime_include = bd.parent.parent / "include"
    return [
        *_tz_defines(),
        f"-I{bd / 'include'}",
        f"-I{runtime_include}",
    ]


def _glue_compile_flags(lib: ThirdPartyLib, mode: ThirdPartyMode) -> list[str]:
    """Flags to build the shim. It includes both its TPy facade header (runtime
    include, always) and <date/tz.h> (vendored include in bundled mode; default
    system paths in system mode)."""
    bd = require_bundled_dir(lib)
    runtime_include = bd.parent.parent / "include"
    flags = [*_tz_defines(), f"-I{runtime_include}"]
    if mode != "system":
        flags.append(f"-I{bd / 'include'}")
    return flags


def factory(runtime_cpp_dir: Path) -> ThirdPartyLib:
    """Build the Hinnant date ``ThirdPartyLib`` declaration.

    Vendored build options picked for TPy's use:
      * tz library on (the calendar half is header-only and unused by
        generated code -- all calendar math is pure TPy);
      * OS tz db -- hermetic on Linux/macOS; Windows would need bundled
        tzdata and is gated on MSVC support anyway (filed).

    No ``bundled_user_include_dir``: dependent TUs include the TPy facade
    only, so upstream headers never enter a generated TU.
    """
    return ThirdPartyLib(
        name="date",
        cli_flag="--date",
        cmake_var="TPY_DATE",
        default_mode="bundled",
        find_package_name="date",
        find_package_target="date::date-tz",
        pkgconfig_name="date",
        system_link_flags=("-ldate-tz",),
        bundled_source_dir=runtime_cpp_dir / "third_party" / "date",
        bundled_cmake_vars={
            "BUILD_TZ_LIB": "ON",
            "USE_SYSTEM_TZ_DB": "ON",
            "ENABLE_DATE_TESTING": "OFF",
            "ENABLE_DATE_INSTALL": "OFF",
        },
        bundled_static_target="date-tz",
        license_file="LICENSE.txt",
        bundled_source_files=_source_files,
        bundled_compile_flags=_compile_flags,
        bundled_user_include_dir=None,
        glue_source_files=_glue_source_files,
        glue_compile_flags=_glue_compile_flags,
    )
