"""zlib-specific build wiring. Registered in
``tpyc/build/third_party.py::_FACTORIES``.

The vendored zlib tree is the core library only (no gzFile API, no
upstream CMake), with a generated static-lib CMakeLists.txt shim (see
``scripts/vendor_zlib.py``). zlib's sources need no configuration
defines, so the direct-compile path only:

  * enumerates the .c files from the sidecar manifest
    ``runtime/cpp/third_party/zlib.sources.txt``;
  * adds ``-I`` on the vendored dir (zlib.h / zconf.h / private headers)
    for the vendored sources and the shim only -- generated TUs include the
    TPy facade (``tpy/stdlib/zlib_h.hpp``), never zlib.h, so dependents get
    no zlib include dir;
  * builds the TPy-owned z_stream shim (``runtime/cpp/src/stdlib/
    zlib_shim.c``) in every mode.
"""

from __future__ import annotations

from pathlib import Path

from .third_party import (
    ThirdPartyLib, ThirdPartyMode, read_source_manifest, require_bundled_dir,
)


_SOURCE_MANIFEST = "zlib.sources.txt"


def _source_files(lib: ThirdPartyLib) -> list[Path]:
    """The zlib .c files to compile, read from the pinned sidecar manifest
    rather than globbed (the tree is pruned, but the manifest keeps the
    build's source set reviewable alongside a version bump)."""
    bd = require_bundled_dir(lib)
    manifest = bd.parent / _SOURCE_MANIFEST
    if not manifest.is_file():
        raise FileNotFoundError(
            f"{lib.name}: source manifest not found at {manifest}"
        )
    paths: list[Path] = []
    for name in read_source_manifest(manifest):
        p = bd / name
        if not p.is_file():
            raise FileNotFoundError(
                f"{lib.name}: expected source file missing: {p} "
                f"(listed in {manifest})"
            )
        paths.append(p)
    return paths


def _compile_flags(lib: ThirdPartyLib) -> list[str]:
    return [f"-I{require_bundled_dir(lib)}"]


def _glue_source_files(lib: ThirdPartyLib) -> list[Path]:
    """The zlib module's FFI shim. Our code, so it links in system mode too;
    a .c file gated on zlib being in use, so not a runtime/cpp/src source."""
    shim = require_bundled_dir(lib).parent.parent / "src" / "stdlib" / "zlib_shim.c"
    if not shim.is_file():
        raise FileNotFoundError(f"{lib.name}: shim not found at {shim}")
    return [shim]


def _glue_compile_flags(lib: ThirdPartyLib, mode: ThirdPartyMode) -> list[str]:
    """The shim includes <zlib.h>: the vendored copy in bundled mode, the
    system one otherwise (resolve_system adds no -I)."""
    if mode == "system":
        return []
    return [f"-I{require_bundled_dir(lib)}"]


def factory(runtime_cpp_dir: Path) -> ThirdPartyLib:
    """Build the zlib ``ThirdPartyLib`` declaration.

    The shim uses only the long-stable z_stream API (inflateInit2 /
    deflateInit2 / inflate / deflate / crc32 / adler32), so any zlib from
    1.2.11 on serves system mode; bundled always builds the vendored copy.
    """
    return ThirdPartyLib(
        name="zlib",
        cli_flag="--zlib",
        cmake_var="TPY_ZLIB",
        default_mode="bundled",
        min_version="1.2.11",
        find_package_name="ZLIB",
        find_package_target="ZLIB::ZLIB",
        pkgconfig_name="zlib",
        system_link_flags=("-lz",),
        bundled_source_dir=runtime_cpp_dir / "third_party" / "zlib",
        bundled_static_target="zlibstatic",
        license_file="LICENSE",
        bundled_source_files=_source_files,
        bundled_compile_flags=_compile_flags,
        bundled_user_include_dir=None,
        glue_source_files=_glue_source_files,
        glue_compile_flags=_glue_compile_flags,
    )
