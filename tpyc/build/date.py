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

from .third_party import ThirdPartyLib, read_source_manifest


_SOURCE_MANIFEST = "date.sources.txt"


def _source_files(lib: ThirdPartyLib) -> list[Path]:
    """The C++ files to compile into the bundled tz backend: upstream
    tz.cpp (per the manifest) plus the TPy-owned facade shim. The shim is
    compiled here, with the vendored sources, so it links only when a
    module declares the dep and gets the same include/define flags -- not
    via discover_runtime_cpp_sources (always-on, which would drag the
    vendored tree into every binary)."""
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    src_dir = lib.bundled_source_dir / "src"
    if not src_dir.is_dir():
        raise FileNotFoundError(
            f"{lib.name}: bundled src/ not found at {src_dir}"
        )
    manifest = lib.bundled_source_dir.parent / _SOURCE_MANIFEST
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
    runtime_cpp = lib.bundled_source_dir.parent.parent
    shim = runtime_cpp / "src" / "stdlib" / "date_shim.cpp"
    if not shim.is_file():
        raise FileNotFoundError(f"{lib.name}: shim not found at {shim}")
    paths.append(shim)
    return paths


def _compile_flags(lib: ThirdPartyLib) -> list[str]:
    """C++-compiler flags for the tz backend sources. USE_OS_TZDB parses
    the OS zoneinfo tree (Linux/macOS; no bundled tzdata, no remote API).
    The runtime include dir lets the shim include its own facade header so
    signature drift is a compile error."""
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    runtime_include = lib.bundled_source_dir.parent.parent / "include"
    return [
        "-DUSE_OS_TZDB=1",
        "-DHAS_REMOTE_API=0",
        f"-I{lib.bundled_source_dir / 'include'}",
        f"-I{runtime_include}",
    ]


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
    )
