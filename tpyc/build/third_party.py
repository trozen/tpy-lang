"""Third-party C/C++ libraries consumed by TPy stdlib modules.

Each vendorable/system library is declared here once. Stdlib `.py` modules
signal their dependency with

    # tpy: link_third_party("pcre2")

and the build layer (CMake emission via `tpyc/compiler.py::generate_cmake`
and the direct-compile path for `tpyc -x` / `-b`) consults the registry to
produce concrete include paths, link flags, and CMake snippets.

Three user-selectable modes per lib:
  * ``bundled`` -- build from the vendored source under
    ``runtime/cpp/third_party/<name>/``. Hermetic, zero system deps. Default.
  * ``system`` -- ``find_package`` in CMake mode, ``-l<lib>`` directly in
    direct-compile mode. Right for downstream C++ projects that already pin
    their own version and want to avoid double-linking.
  * ``auto`` -- try system first, fall back to bundled.

v1 is deliberately narrow: a single ``PCRE2`` factory and a one-entry
lookup map. The shape anticipates multi-lib registration, but we avoid the
loop + auto-CLI-flag-registration machinery until a second lib (zlib,
sqlite3, libuv) arrives and drives what needs to vary.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


ThirdPartyMode = Literal["bundled", "system", "auto", "none"]
THIRD_PARTY_MODES: tuple[ThirdPartyMode, ...] = ("bundled", "system", "auto", "none")


class DisabledLibError(Exception):
    """Raised when the user explicitly disables a third-party lib (--<lib>=none)
    but a compiled module declares a dependency on it. Surfaced by the CLI
    as a user-facing compile error before reaching the build stage."""
    def __init__(self, lib_name: str, cli_flag: str) -> None:
        super().__init__(
            f"'{lib_name}' is disabled ({cli_flag}=none) but is required by an "
            f"imported module (declared via `# tpy: link(\"{lib_name}\", "
            f"managed=True)`). Re-run with {cli_flag}=bundled, {cli_flag}=system, "
            f"or {cli_flag}=auto; or remove the `import` chain that pulls it in."
        )
        self.lib_name = lib_name
        self.cli_flag = cli_flag


@dataclass(frozen=True)
class ThirdPartyLib:
    """Declaration of a single third-party C/C++ dependency."""

    # Identity
    name: str                                   # "pcre2"
    cli_flag: str                               # "--pcre2"
    cmake_var: str                              # "TPY_PCRE2"
    default_mode: ThirdPartyMode = "bundled"
    min_version: str | None = None

    # System-mode discovery (CMake).
    find_package_name: str = ""                 # "PCRE2"
    find_package_components: tuple[str, ...] = ()
    find_package_target: str = ""               # "PCRE2::8BIT"
    # System-mode discovery (direct-compile). Minimal today: plain -l flags.
    # TODO: probe pkg-config / brew prefix for include + link flags before
    # falling back to bare `-l<name>`. Worth doing once a system-mode test
    # variant exists and a real user hits the "couldn't find headers" case.
    pkgconfig_name: str | None = None
    system_link_flags: tuple[str, ...] = ()

    # Bundled mode. ``None`` -> system-only (e.g. OpenSSL, which we never
    # want to vendor because of its size and security-update cadence).
    bundled_source_dir: Path | None = None
    bundled_cmake_vars: dict[str, str] = field(default_factory=dict)
    bundled_static_target: str = ""             # CMake target exported by the
                                                # vendored sub-build
    license_file: str = "LICENSE"


# ---------------------------------------------------------------------------
# PCRE2 declaration
# ---------------------------------------------------------------------------

def _pcre2(runtime_cpp_dir: Path) -> ThirdPartyLib:
    """PCRE2 declaration.

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
    )


_FACTORIES: dict[str, Callable[[Path], ThirdPartyLib]] = {
    "pcre2": _pcre2,
}


def known_lib_names() -> list[str]:
    """Names of all declared third-party libraries, sorted."""
    return sorted(_FACTORIES)


def get_lib(name: str, runtime_cpp_dir: Path) -> ThirdPartyLib:
    """Look up a third-party library declaration by name.

    ``runtime_cpp_dir`` is the ``runtime/cpp`` directory (parent of
    ``include/`` and ``third_party/``), used to resolve vendored source
    locations.
    """
    factory = _FACTORIES.get(name)
    if factory is None:
        raise KeyError(
            f"unknown third-party library: {name!r} "
            f"(known: {sorted(_FACTORIES)})"
        )
    return factory(runtime_cpp_dir)


# ---------------------------------------------------------------------------
# CMake snippet emission
# ---------------------------------------------------------------------------

def emit_cmake_snippet(lib: ThirdPartyLib) -> str:
    """Emit the 3-mode CMake selector for one library.

    Produces a fragment intended to be inserted into ``sources.cmake`` after
    the ``set(TPYC_LIBRARIES ...)`` block. The fragment:

      * declares a cache variable named ``<lib.cmake_var>``, default set to
        ``lib.default_mode``, user-overridable via
        ``-D<cmake_var>=system`` at configure time;
      * in ``system`` mode: calls ``find_package(... REQUIRED)``;
      * in ``bundled`` mode: ``add_subdirectory()``s the vendored copy under
        ``third_party/<name>/`` next to ``sources.cmake``;
      * in ``auto`` mode: ``find_package(... QUIET)`` with bundled fallback;
      * appends the resolved target to ``TPYC_LIBRARIES``.
    """
    upper = lib.name.upper()
    target_var = f"TPY_{upper}_TARGET"
    ver = f" {lib.min_version}" if lib.min_version else ""
    comps = " ".join(lib.find_package_components)
    comp_arg = f" COMPONENTS {comps}" if comps else ""
    cmake_dir = "${CMAKE_CURRENT_LIST_DIR}"

    def _bundled_block(indent: str) -> list[str]:
        out_: list[str] = []
        for var, val in lib.bundled_cmake_vars.items():
            out_.append(f'{indent}set({var} {val} CACHE BOOL "" FORCE)')
        out_.append(
            f'{indent}add_subdirectory({cmake_dir}/third_party/{lib.name}'
        )
        out_.append(
            f'{indent}                 '
            f'${{CMAKE_BINARY_DIR}}/third_party/{lib.name} EXCLUDE_FROM_ALL)'
        )
        out_.append(f'{indent}set({target_var} {lib.bundled_static_target})')
        return out_

    out: list[str] = []
    out.append(f'# --- {lib.name} (via # tpy: link_third_party) ---')
    out.append(
        f'set({lib.cmake_var} "{lib.default_mode}" CACHE STRING '
        f'"Source for {lib.name}: bundled | system | auto")'
    )
    out.append(f'if({lib.cmake_var} STREQUAL "system")')
    out.append(
        f'    find_package({lib.find_package_name}{ver} REQUIRED{comp_arg})'
    )
    out.append(f'    set({target_var} {lib.find_package_target})')
    if lib.bundled_source_dir is not None:
        out.append(f'elseif({lib.cmake_var} STREQUAL "bundled")')
        out.extend(_bundled_block("    "))
        out.append('else()  # auto')
        out.append(
            f'    find_package({lib.find_package_name}{ver} QUIET{comp_arg})'
        )
        out.append(f'    if({lib.find_package_name}_FOUND)')
        out.append(f'        set({target_var} {lib.find_package_target})')
        out.append('    else()')
        out.extend(_bundled_block("        "))
        out.append('    endif()')
    out.append('endif()')
    out.append(f'list(APPEND TPYC_LIBRARIES ${{{target_var}}})')
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Bundling + licensing
# ---------------------------------------------------------------------------

# Patterns we drop when copying vendored source into a user-facing bundled
# output. Keeps the emitted tree small and free of dev-only cruft.
_BUNDLE_IGNORE = shutil.ignore_patterns(
    ".git", ".github", ".gitignore", ".gitattributes",
    "__pycache__", "*.pyc",
    "testdata", "testinput*", "testoutput*",
)


def bundle_source_tree(lib: ThirdPartyLib, output_root: Path) -> Path | None:
    """Copy the lib's vendored source tree to
    ``output_root/third_party/<name>/``.

    No-op for libraries without a bundled source tree (system-only). Raises
    ``FileNotFoundError`` if the lib is declared bundled but the vendored
    source is missing on disk. Returns the destination path, or None.
    """
    if lib.bundled_source_dir is None:
        return None
    if not lib.bundled_source_dir.exists():
        raise FileNotFoundError(
            f"vendored source for {lib.name!r} not found at "
            f"{lib.bundled_source_dir}. Run the PCRE2 vendoring step or pass "
            f"--{lib.name}=system to use the system library."
        )
    dst = output_root / "third_party" / lib.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        lib.bundled_source_dir, dst, symlinks=True, ignore=_BUNDLE_IGNORE
    )
    return dst


def collect_licenses(
    libs: list[ThirdPartyLib], output_root: Path
) -> Path | None:
    """Aggregate LICENSE files from all used libs into
    ``output_root/THIRD_PARTY_LICENSES/<name>.txt``.

    Only libs with a bundled source tree contribute -- in system mode, the
    host OS's packaging provides its own license notice. Returns the
    destination directory, or None if nothing was written.
    """
    if not libs:
        return None
    dst = output_root / "THIRD_PARTY_LICENSES"
    dst.mkdir(parents=True, exist_ok=True)
    wrote_any = False
    for lib in libs:
        if lib.bundled_source_dir is None:
            continue
        src = lib.bundled_source_dir / lib.license_file
        if src.exists():
            shutil.copy2(src, dst / f"{lib.name}.txt")
            wrote_any = True
    return dst if wrote_any else None


# ---------------------------------------------------------------------------
# Direct-compile flag resolution (for tpyc -x / -b path, no CMake layer)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedLib:
    """Mode-resolved direct-compile flags for a single library.

    The actual orchestration that *builds* bundled libraries from source
    (producing a .a file for the linker) lives in ``tpyc/compiler.py`` --
    this struct just describes the include and link flags the compiler
    should add to the command line, given an already-built bundled artifact.
    """
    lib: ThirdPartyLib
    mode: ThirdPartyMode
    include_flags: tuple[str, ...]
    link_flags: tuple[str, ...]


def resolve_system(lib: ThirdPartyLib) -> ResolvedLib:
    """System-mode direct-compile flags.

    Minimal v1: emit the declared ``system_link_flags`` and rely on the
    system compiler finding headers via standard search paths.

    TODO: pkg-config / brew prefix probing for include flags + link line.
    Same item as the ``ThirdPartyLib.pkgconfig_name`` note above; lift to
    a real implementation when a system-mode test variant lands or a user
    reports a missing-header on a non-default-prefix install.
    """
    return ResolvedLib(
        lib=lib, mode="system",
        include_flags=(),
        link_flags=lib.system_link_flags,
    )


def resolve_bundled_flags(
    lib: ThirdPartyLib,
    bundled_include_dir: Path,
    bundled_static_lib: Path,
) -> ResolvedLib:
    """Bundled-mode direct-compile flags, given already-built artifacts.

    Caller is responsible for building the static library (typically via
    shelling out to CMake into the stdlib object cache) and for placing the
    resulting .a file at ``bundled_static_lib``.
    """
    return ResolvedLib(
        lib=lib, mode="bundled",
        include_flags=(f"-I{bundled_include_dir}",),
        link_flags=(str(bundled_static_lib),),
    )


# ---------------------------------------------------------------------------
# PCRE2 bundled-build helpers (direct-compile path; no CMake required).
#
# PCRE2 ships a "non-autotools" build path: rename a few `.generic` /
# `.dist` template files, then compile the .c files with -DPCRE2_CODE_UNIT_WIDTH=8
# and -DHAVE_CONFIG_H. We pre-materialized the templates at vendoring time, so
# all the build path needs to do is enumerate the sources + flags.
# ---------------------------------------------------------------------------

# Source files that compose libpcre2-8. From PCRE2's NON-AUTOTOOLS-BUILD
# documentation -- this is the canonical source list for an 8-bit build.
# Files in src/ that are NOT in this list are either standalone tools
# (pcre2grep.c, pcre2test.c, pcre2demo.c), test harnesses (pcre2_jit_test.c,
# pcre2posix_test.c, pcre2_fuzzsupport.c), build helpers (pcre2_dftables.c
# generates pcre2_chartables.c -- we ship the pre-generated file), the
# POSIX wrapper (pcre2posix.c -- a separate library), or files that are
# #included by other .c files (pcre2_printint.c is included by pcre2test
# and pcre2_dftables; pcre2_jit_match.c and pcre2_jit_misc.c are included
# by pcre2_jit_compile.c; pcre2_ucptables.c is included by pcre2_tables.c).
_PCRE2_8_SOURCES: tuple[str, ...] = (
    "pcre2_auto_possess.c",
    "pcre2_chartables.c",
    "pcre2_chkdint.c",
    "pcre2_compile.c",
    "pcre2_config.c",
    "pcre2_context.c",
    "pcre2_convert.c",
    "pcre2_dfa_match.c",
    "pcre2_error.c",
    "pcre2_extuni.c",
    "pcre2_find_bracket.c",
    "pcre2_jit_compile.c",
    "pcre2_maketables.c",
    "pcre2_match.c",
    "pcre2_match_data.c",
    "pcre2_newline.c",
    "pcre2_ord2utf.c",
    "pcre2_pattern_info.c",
    "pcre2_script_run.c",
    "pcre2_serialize.c",
    "pcre2_string_utils.c",
    "pcre2_study.c",
    "pcre2_substitute.c",
    "pcre2_substring.c",
    "pcre2_tables.c",
    "pcre2_ucd.c",
    "pcre2_valid_utf.c",
    "pcre2_xclass.c",
)


def pcre2_source_files(lib: ThirdPartyLib) -> list[Path]:
    """Return the list of PCRE2 .c files to compile into libpcre2-8.

    Uses the explicit source list from PCRE2's NON-AUTOTOOLS-BUILD doc rather
    than globbing -- avoids accidentally pulling in #included-by-others files
    (pcre2_printint.c, pcre2_jit_match.c, etc.) when PCRE2 grows new sources.
    """
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    src_dir = lib.bundled_source_dir / "src"
    if not src_dir.is_dir():
        raise FileNotFoundError(
            f"{lib.name}: bundled src/ not found at {src_dir}"
        )
    paths: list[Path] = []
    for name in _PCRE2_8_SOURCES:
        p = src_dir / name
        if not p.is_file():
            raise FileNotFoundError(
                f"{lib.name}: expected source file missing: {p}"
            )
        paths.append(p)
    return paths


def pcre2_compile_flags(lib: ThirdPartyLib) -> list[str]:
    """Return the C-compiler flags needed to build a PCRE2 .c file.

    -I<src_dir> is required so PCRE2's internal headers (pcre2_internal.h,
    pcre2.h, config.h) resolve. -DHAVE_CONFIG_H tells PCRE2 to include
    config.h (which we materialized from config.h.generic at vendoring time
    with SUPPORT_PCRE2_8 / SUPPORT_JIT / SUPPORT_UNICODE enabled).
    """
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    src_dir = lib.bundled_source_dir / "src"
    return [
        "-DHAVE_CONFIG_H",
        "-DPCRE2_CODE_UNIT_WIDTH=8",
        f"-I{src_dir}",
    ]


def pcre2_user_include_dir(lib: ThirdPartyLib) -> Path:
    """Path containing pcre2.h for user code to #include.

    Same dir as the source files; PCRE2 keeps everything in src/.
    """
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    return lib.bundled_source_dir / "src"


# ---------------------------------------------------------------------------
# Build-plan resolution (top-level entry point for the compile/link path)
# ---------------------------------------------------------------------------

@dataclass
class ThirdPartyBuildPlan:
    """Concrete build inputs for a set of declared third-party deps.

    Returned by ``resolve_build_plan`` and consumed by both the direct-compile
    path (passing ``c_sources`` + ``extra_include_dirs`` + ``extra_link_flags``
    to ``BuildLayout.build_cpp_commands``) and the CMake-emit path (passing
    ``libs`` to ``BuildLayout.generate_cmake``).
    """
    libs: list[ThirdPartyLib] = field(default_factory=list)
    c_sources: list[tuple[Path, list[str]]] = field(default_factory=list)
    extra_include_dirs: list[Path] = field(default_factory=list)
    extra_link_flags: list[str] = field(default_factory=list)


def resolve_build_plan(
    dep_names: list[str],
    runtime_cpp_dir: Path,
    modes: dict[str, ThirdPartyMode],
) -> ThirdPartyBuildPlan:
    """Resolve declared third-party deps into concrete build inputs.

    ``dep_names``: ordered, deduplicated list of lib names (from
    ``Compiler.collect_third_party_deps()``).

    ``modes``: per-lib mode override (e.g. ``{"pcre2": "system"}`` from
    ``--pcre2=system``). Falls back to each lib's ``default_mode``.

    TODO: auto mode currently resolves to bundled in the direct-compile
    path (system probing not implemented). The CMake path's
    ``find_package(... QUIET)`` already does the genuine probe; bring the
    direct-compile path to parity by attempting pkg-config / standard
    include + link probe before falling back to bundled.
    """
    plan = ThirdPartyBuildPlan()
    for name in dep_names:
        lib = get_lib(name, runtime_cpp_dir)
        mode = modes.get(name, lib.default_mode)
        if mode == "none":
            raise DisabledLibError(name, lib.cli_flag)
        plan.libs.append(lib)
        if mode == "system":
            plan.extra_link_flags.extend(lib.system_link_flags)
            continue

        # Bundled (or auto, treated as bundled in direct-compile for now).
        # TODO: replace this name-dispatch with per-ThirdPartyLib callables
        # (e.g. ``ThirdPartyLib.bundled_source_files``) when a second
        # bundled-capable lib lands -- avoids growing this if/elif chain
        # and keeps lib-specific glue next to the lib's declaration.
        if name == "pcre2":
            for src in pcre2_source_files(lib):
                plan.c_sources.append((src, pcre2_compile_flags(lib)))
            plan.extra_include_dirs.append(pcre2_user_include_dir(lib))
        else:
            raise NotImplementedError(
                f"bundled-mode direct compile not implemented for {name!r}"
            )
    return plan
