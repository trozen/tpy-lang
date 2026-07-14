"""Generic registry for third-party C/C++ libraries consumed by TPy
stdlib modules.

Each vendorable/system library is declared once as a ``ThirdPartyLib``.
Stdlib `.py` modules signal their dependency with

    # tpy: link("pcre2", managed=True)

and the build layer (CMake emission via ``tpyc/compiler.py::generate_cmake``
and the direct-compile path for ``tpyc -x`` / ``-b``) consults the
registry to produce concrete include paths, link flags, and CMake
snippets.

Three user-selectable modes per lib:
  * ``bundled`` -- build from the vendored source under
    ``runtime/cpp/third_party/<name>/``. Hermetic, zero system deps. Default.
  * ``system`` -- ``find_package`` in CMake mode, ``-l<lib>`` directly in
    direct-compile mode. Right for downstream C++ projects that already
    pin their own version and want to avoid double-linking.
  * ``auto`` -- try system first, fall back to bundled.

Per-library specifics (PCRE2's source list, its compile flags, etc.)
live in sibling modules (``tpyc/build/pcre2.py``, etc.); this file keeps
only the machinery that's shared across any managed lib.
"""

from __future__ import annotations

import shutil
import subprocess
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


class SystemLibVersionError(Exception):
    """Raised when a system library (--<lib>=system) is affirmatively older than
    the version TPy's glue targets. Without this the build fails deep in the C
    compiler with a cryptic API-mismatch error (e.g. Ubuntu 24.04 ships mbedTLS
    2.28, but the ssl shim targets 3.6); this turns it into a clear diagnostic
    pointing at bundled mode."""
    def __init__(self, lib_name: str, cli_flag: str, found: str, need: str) -> None:
        super().__init__(
            f"system '{lib_name}' is version {found}, but TPy requires >= {need} "
            f"({cli_flag}=system). Re-run with {cli_flag}=bundled (the default, "
            f"hermetic), or install {lib_name} >= {need}."
        )
        self.lib_name = lib_name
        self.cli_flag = cli_flag
        self.found = found
        self.need = need


@dataclass(frozen=True)
class ThirdPartyLib:
    """Declaration of a single third-party C/C++ dependency.

    Lib-specific build knowledge (what source files to compile, what -I /
    -D flags they need, what dir holds the public headers) lives behind
    the ``bundled_*`` callables rather than in the registry, so adding
    a new managed lib means a new factory module plus one row in
    ``_FACTORIES``, not edits to the generic resolver.
    """

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

    # Per-lib callables for the direct-compile bundled path. Each takes
    # the lib itself and returns the lib-specific answer:
    #   * bundled_source_files   -- list of source files to compile (.c or
    #                               .cpp; driver picked per suffix by
    #                               compiler.third_party_source_driver)
    #   * bundled_compile_flags  -- list of -I / -D / ... flags for those
    #                               sources (same flags for all sources
    #                               in v1; refine if any lib needs per-file)
    #   * bundled_user_include_dir -- path containing the public headers
    #                                 that dependents #include
    # None means "bundled direct-compile not supported for this lib"
    # (either no bundled source dir, or CMake-only bundled build).
    bundled_source_files: Callable[["ThirdPartyLib"], list[Path]] | None = None
    bundled_compile_flags: Callable[["ThirdPartyLib"], list[str]] | None = None
    bundled_user_include_dir: Callable[["ThirdPartyLib"], Path] | None = None

    # TPy-owned glue (the ssl/datetime FFI shim + any data blob it needs).
    # Mode-INDEPENDENT: it is our code, not the upstream library, so it must
    # compile and link in system mode too -- only the vendored upstream
    # sources are mode-gated. glue_compile_flags takes the resolved mode
    # because the glue's headers resolve against the vendored include in
    # bundled mode and system paths in system mode.
    glue_source_files: Callable[["ThirdPartyLib"], list[Path]] | None = None
    glue_compile_flags: (
        Callable[["ThirdPartyLib", "ThirdPartyMode"], list[str]] | None
    ) = None


# ---------------------------------------------------------------------------
# Per-lib factory helpers
# ---------------------------------------------------------------------------

def require_bundled_dir(lib: ThirdPartyLib) -> Path:
    """The lib's vendored source dir, or a clear error. Every per-lib source /
    flag callable reaches through it, so centralize the None guard here."""
    if lib.bundled_source_dir is None:
        raise RuntimeError(f"{lib.name}: no bundled source dir configured")
    return lib.bundled_source_dir


def read_source_manifest(path: Path) -> list[str]:
    """Read a source-list manifest (one filename per line, ``#`` comments,
    blank lines ignored). Used by per-lib factories that keep the source
    list as a sidecar file next to the vendored tree rather than embedded
    in Python."""
    names: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)
    return names


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Per-lib factories are imported lazily on first lookup so their source
# files can depend on names defined later in this module (ThirdPartyLib,
# read_source_manifest) without needing mid-module imports.

_FACTORIES: dict[str, Callable[[Path], ThirdPartyLib]] | None = None


def _factories() -> dict[str, Callable[[Path], ThirdPartyLib]]:
    global _FACTORIES
    if _FACTORIES is None:
        from . import pcre2 as _pcre2
        from . import mbedtls as _mbedtls
        from . import date as _date
        _FACTORIES = {"pcre2": _pcre2.factory, "mbedtls": _mbedtls.factory,
                      "date": _date.factory}
    return _FACTORIES


def known_lib_names() -> list[str]:
    """Names of all declared third-party libraries, sorted."""
    return sorted(_factories())


def get_lib(name: str, runtime_cpp_dir: Path) -> ThirdPartyLib:
    """Look up a third-party library declaration by name.

    ``runtime_cpp_dir`` is the ``runtime/cpp`` directory (parent of
    ``include/`` and ``third_party/``), used to resolve vendored source
    locations.
    """
    factories = _factories()
    factory = factories.get(name)
    if factory is None:
        raise KeyError(
            f"unknown third-party library: {name!r} "
            f"(known: {sorted(factories)})"
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
    out.append(f'# --- {lib.name} (via # tpy: link(..., managed=True)) ---')
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
            f"{lib.bundled_source_dir}. Run scripts/vendor_{lib.name}.py or "
            f"pass {lib.cli_flag}=system to use the system library."
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


def _parse_version(v: str) -> tuple[int, ...]:
    """Lenient dotted-version parse: '2.28.8' -> (2, 28, 8). Stops at the first
    non-digit within a component ('3.6.0-rc1' -> (3, 6, 0)); good enough for the
    >= comparison against a declared min_version."""
    out: list[int] = []
    for tok in v.split("."):
        digits = ""
        for ch in tok:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out)


def system_version_too_old(lib: ThirdPartyLib) -> str | None:
    """Return the detected system version string if it is affirmatively older
    than lib.min_version, else None.

    Probes pkg-config --modversion. When pkg-config, the .pc file, or
    min_version is unavailable the version can't be determined, so we stay
    permissive (a from-source or non-pkg-config install may still be fine) and
    return None -- we only block the case we KNOW is too old.
    """
    if not lib.min_version or not lib.pkgconfig_name:
        return None
    try:
        r = subprocess.run(
            ["pkg-config", "--modversion", lib.pkgconfig_name],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    found = r.stdout.strip()
    if not found:
        return None
    fv, mv = _parse_version(found), _parse_version(lib.min_version)
    # Zero-pad to equal length so a short-form report ("2.28") is not read as
    # older than the same version with a patch component ("2.28.0").
    width = max(len(fv), len(mv))
    fv += (0,) * (width - len(fv))
    mv += (0,) * (width - len(mv))
    return found if fv < mv else None


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
        if mode == "system":
            # Fail early with a clear message rather than deep in the C compiler
            # when the system lib is older than the glue targets.
            too_old = system_version_too_old(lib)
            if too_old is not None:
                raise SystemLibVersionError(
                    name, lib.cli_flag, too_old, lib.min_version or "?",
                )

        plan.libs.append(lib)

        # TPy-owned glue compiles in every mode -- it is our FFI surface, not
        # the upstream library. Skipping it in system mode leaves the whole
        # stdlib .o set with undefined references to it (e.g. tpy_tls_*).
        if lib.glue_source_files is not None:
            glue_flags = (lib.glue_compile_flags(lib, mode)
                          if lib.glue_compile_flags is not None else [])
            for src in lib.glue_source_files(lib):
                plan.c_sources.append((src, glue_flags))

        if mode == "system":
            plan.extra_link_flags.extend(lib.system_link_flags)
            continue

        # Bundled (or auto, treated as bundled in direct-compile for now).
        # Per-lib source list / compile flags / include dir come from the
        # callables on the lib; generic resolver stays neutral.
        if lib.bundled_source_files is None:
            raise NotImplementedError(
                f"bundled-mode direct compile not implemented for {name!r} "
                f"(no bundled_source_files callable on the lib declaration)"
            )
        flags = (lib.bundled_compile_flags(lib)
                 if lib.bundled_compile_flags is not None else [])
        for src in lib.bundled_source_files(lib):
            plan.c_sources.append((src, flags))
        if lib.bundled_user_include_dir is not None:
            plan.extra_include_dirs.append(lib.bundled_user_include_dir(lib))
    return plan
