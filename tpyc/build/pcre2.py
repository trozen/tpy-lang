"""PCRE2-specific build wiring. Registered in
``tpyc/build/third_party.py::_FACTORIES``.

PCRE2 ships a "non-autotools" build path: rename a few ``.generic`` /
``.dist`` template files, then compile the .c files with
``-DPCRE2_CODE_UNIT_WIDTH=8`` and ``-DHAVE_CONFIG_H``. We pre-materialize
the templates at vendoring time (``scripts/vendor_pcre2.py``), so all
the direct-compile path does here is:

  * enumerate the .c files (from the sidecar manifest
    ``runtime/cpp/third_party/pcre2.sources.txt``, next to the vendored
    tree to keep the upstream tree pristine);
  * emit the fixed -I / -D flags;
  * point dependents at the include dir that holds pcre2.h.
"""

from __future__ import annotations

from pathlib import Path

from .third_party import ThirdPartyLib, read_source_manifest


# Manifest lives next to the vendored tree, not inside it, so the
# upstream tree stays pristine (same reason pcre2.vendor.json lives
# outside). Filename mirrors the vendor.json sidecar.
_SOURCE_MANIFEST = "pcre2.sources.txt"


def _source_files(lib: ThirdPartyLib) -> list[Path]:
    """Return the list of PCRE2 .c files to compile into libpcre2-8.

    Reads the sidecar manifest next to the vendored source tree rather
    than globbing -- globbing would sweep in files that aren't meant to
    be compiled directly (standalone tools, test harnesses, .c files
    that exist only to be #included by others). The manifest is the
    canonical source list from upstream PCRE2's NON-AUTOTOOLS-BUILD doc.
    """
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
    return paths


def _compile_flags(lib: ThirdPartyLib) -> list[str]:
    """C-compiler flags needed to build a PCRE2 .c file.

    -I<src_dir> is required so PCRE2's internal headers (pcre2_internal.h,
    pcre2.h, config.h) resolve. -DHAVE_CONFIG_H tells PCRE2 to include
    config.h (which we materialized from config.h.generic at vendoring
    time with SUPPORT_PCRE2_8 / SUPPORT_JIT / SUPPORT_UNICODE enabled).
    """
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    return [
        "-DHAVE_CONFIG_H",
        "-DPCRE2_CODE_UNIT_WIDTH=8",
        f"-I{lib.bundled_source_dir / 'src'}",
    ]


def _user_include_dir(lib: ThirdPartyLib) -> Path:
    """Path containing pcre2.h for user code to ``#include``.
    Same dir as the source files; PCRE2 keeps everything in ``src/``."""
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    return lib.bundled_source_dir / "src"


def factory(runtime_cpp_dir: Path) -> ThirdPartyLib:
    """Build the PCRE2 ``ThirdPartyLib`` declaration.

    Vendored build options picked for TPy's use:
      * 8-bit only -- Python str maps to UTF-8 bytes; 16/32-bit unused.
      * JIT on -- ~10x match-time speedup, widely supported.
      * Tests + pcre2grep off -- we validate via our own test cases, and
        the grep CLI utility doesn't belong in a runtime build.
    """
    return ThirdPartyLib(
        name="pcre2",
        cli_flag="--pcre2",
        cmake_var="TPY_PCRE2",
        default_mode="bundled",
        min_version="10.35",
        find_package_name="PCRE2",
        find_package_components=("8BIT",),
        find_package_target="PCRE2::8BIT",
        pkgconfig_name="libpcre2-8",
        system_link_flags=("-lpcre2-8",),
        bundled_source_dir=runtime_cpp_dir / "third_party" / "pcre2",
        bundled_cmake_vars={
            "PCRE2_BUILD_PCRE2_8": "ON",
            "PCRE2_BUILD_PCRE2_16": "OFF",
            "PCRE2_BUILD_PCRE2_32": "OFF",
            "PCRE2_SUPPORT_JIT": "ON",
            "PCRE2_BUILD_TESTS": "OFF",
            "PCRE2_BUILD_PCRE2GREP": "OFF",
        },
        bundled_static_target="pcre2-8-static",
        license_file="LICENCE",
        bundled_source_files=_source_files,
        bundled_compile_flags=_compile_flags,
        bundled_user_include_dir=_user_include_dir,
    )
