"""
C++ toolchain discovery and configuration.

Compiler resolution (--cxx / $CXX / auto-detect), CppCompilerConfig, the
strict warning sets, PCH building, and runtime-source discovery. Split out
of compiler.py so the build cache's warm path can replay toolchain
resolution without importing the compiler machinery (~250ms) -- keep this
module's imports light (stdlib only).
"""

from __future__ import annotations
import functools
import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class CompilerNotFoundError(Exception):
    """Raised when a requested C++ compiler cannot be found."""
    def __init__(self, cxx: str):
        self.cxx = cxx
        super().__init__(f"C++ compiler '{cxx}' not found")


def _find_all_versioned(prefix: str) -> list[tuple[str, str, int]]:
    """Find all versioned binaries matching prefix, sorted by version descending.

    Returns list of (binary_name, path, version). Unversioned binary gets version -1.
    """
    seen: dict[str, tuple[str, int]] = {}
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for fpath in glob.glob(os.path.join(d, f"{prefix}-*")):
            name = os.path.basename(fpath)
            suffix = name[len(prefix) + 1:]
            try:
                ver = int(suffix)
            except ValueError:
                continue
            if name not in seen:
                path = shutil.which(name)
                if path:
                    seen[name] = (path, ver)
    # Unversioned
    if prefix not in seen:
        path = shutil.which(prefix)
        if path:
            seen[prefix] = (path, -1)
    return [(name, path, ver) for name, (path, ver) in
            sorted(seen.items(), key=lambda x: -x[1][1])]


def _find_best_versioned(prefix: str) -> str | None:
    """Find the highest-versioned binary matching prefix (e.g. 'g++' -> 'g++-14')."""
    entries = _find_all_versioned(prefix)
    return entries[0][0] if entries else None


def _find_zig() -> str | None:
    """Find the zig binary on PATH or inside the ziglang PyPI package."""
    system, bundled = _find_all_zig()
    return system or bundled


def _find_all_zig() -> tuple[str | None, str | None]:
    """Find system and bundled zig binaries. Returns (system_path, bundled_path)."""
    system = shutil.which("zig")
    bundled = None
    try:
        import ziglang
        candidate = os.path.join(os.path.dirname(ziglang.__file__), "zig")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            # Don't count system zig as bundled
            if candidate != system:
                bundled = candidate
    except ImportError:
        pass
    return system, bundled


def _resolve_compiler(cxx: str) -> list[str] | None:
    """Resolve a --cxx value to a compiler command list, or None if not found.

    Accepted forms:
      gcc, g++                -> best versioned g++
      gcc-14, g++-14          -> specific version
      clang, clang++          -> best versioned clang++
      clang-18, clang++-18    -> specific version
      zig                     -> zig c++
      /path/to/compiler       -> literal path
      any-binary-name         -> looked up on PATH
    """
    # Path (absolute or relative with /)
    if "/" in cxx:
        if os.path.isfile(cxx) and os.access(cxx, os.X_OK):
            return [cxx]
        return None

    if cxx in ("gcc", "g++"):
        name = _find_best_versioned("g++")
        return [name] if name else None
    if cxx in ("clang", "clang++"):
        name = _find_best_versioned("clang++")
        return [name] if name else None
    if cxx == "zig":
        zig = _find_zig()
        return [zig, "c++"] if zig else None
    if cxx == "zig-bundled":
        _, bundled = _find_all_zig()
        return [bundled, "c++"] if bundled else None

    # clang-repl is a REPL JIT backend, not a batch compiler
    if cxx == "clang-repl" or cxx.startswith("clang-repl-"):
        return None

    # gcc-14 -> g++-14, clang-18 -> clang++-18
    if cxx.startswith("gcc-"):
        binary = "g++-" + cxx[4:]
        if shutil.which(binary):
            return [binary]
        return None
    if cxx.startswith("clang-"):
        binary = "clang++-" + cxx[6:]
        if shutil.which(binary):
            return [binary]
        return None

    # Already a binary name (g++-14, clang++-18, etc.)
    if shutil.which(cxx):
        return [cxx]
    return None


def _auto_detect_compiler() -> list[str]:
    """Auto-detect the best available C++ compiler for building."""
    for prefix in ["g++", "clang++"]:
        name = _find_best_versioned(prefix)
        if name:
            return [name]
    zig = _find_zig()
    if zig:
        return [zig, "c++"]
    return ["g++"]


def _is_zig(compiler: list[str]) -> bool:
    return "zig" in os.path.basename(compiler[0])


def third_party_source_driver(src: Path, cxx: list[str], std: str) -> list[str]:
    """Compiler driver for one bundled third-party source file.

    `.cpp` sources (e.g. Hinnant date's tz.cpp and the datetime tz shim)
    use the C++ driver with the configured -std; anything else uses the C
    compiler derived from it (C++ drivers reject implicit ``void*``
    conversions that PCRE2 relies on). Shared by `build_cpp_commands` and
    the test harness's stdlib-cache pre-compile so the dispatch rule can't
    drift between the two build paths.
    """
    if src.suffix == ".cpp":
        return [*cxx, f"-std={std}"]
    return _derive_c_compiler(cxx)


def _derive_c_compiler(cxx: list[str]) -> list[str]:
    """Derive the matching C compiler command from a C++ compiler command.

    Used when building bundled C dependencies (e.g. PCRE2). The C and C++
    drivers share a toolchain but differ in default language: g++ would
    reject PCRE2's implicit ``void*`` conversions that gcc accepts.

    Mappings:
      ['g++']            -> ['gcc']
      ['g++-14']         -> ['gcc-14']
      ['clang++']        -> ['clang']
      ['clang++-18']     -> ['clang-18']
      ['zig', 'c++']     -> ['zig', 'cc']
      ['/p/g++-14']      -> ['/p/gcc-14']

    Falls back to the original command if the pattern isn't recognized
    (e.g. an unusual binary name); the C++ driver may still compile most C
    correctly even if it grumbles.
    """
    if not cxx:
        return cxx
    head = cxx[0]
    rest = cxx[1:]
    # Zig: ['zig', 'c++'] -> ['zig', 'cc']
    if _is_zig(cxx) and rest and rest[0] == "c++":
        return [head, "cc", *rest[1:]]
    base = os.path.basename(head)
    dirname = os.path.dirname(head)
    if base.startswith("g++"):
        c_base = "gcc" + base[3:]
    elif base.startswith("clang++"):
        c_base = "clang" + base[7:]
    else:
        return cxx
    new_head = os.path.join(dirname, c_base) if dirname else c_base
    return [new_head, *rest]


def _cxx_aliases(binary: str, is_best: bool, family_prefix: str) -> list[str]:
    """Compute --cxx aliases for a compiler binary.

    E.g. g++-14 (best) -> gcc, gcc-14; g++-13 -> gcc-13
    """
    aliases: list[str] = []
    # gcc/clang short alias only for the best version
    if is_best:
        if family_prefix == "g++":
            aliases.append("gcc")
        elif family_prefix == "clang++":
            aliases.append("clang")
    # Versioned alias: g++-14 -> gcc-14, clang++-18 -> clang-18
    if "-" in binary:
        ver = binary.split("-", 1)[1]
        if family_prefix == "g++":
            aliases.append(f"gcc-{ver}")
        elif family_prefix == "clang++":
            aliases.append(f"clang-{ver}")
    # The binary name itself
    aliases.append(binary)
    return aliases


def list_compilers() -> None:
    """Print available C++ compilers to stdout."""
    auto = _auto_detect_compiler()
    auto_display = os.path.basename(auto[0])
    if len(auto) > 1:
        auto_display += " " + " ".join(auto[1:])

    entries: list[tuple[str, str, list[str], str]] = []  # (binary, path, aliases, note)

    for prefix in ["g++", "clang++"]:
        all_vers = _find_all_versioned(prefix)
        for i, (name, path, _ver) in enumerate(all_vers):
            aliases = _cxx_aliases(name, is_best=(i == 0), family_prefix=prefix)
            entries.append((name, path, aliases, ""))

    for i, (name, path, _ver) in enumerate(_find_all_versioned("clang-repl")):
        if i == 0:
            aliases = ["clang-repl", name] if name != "clang-repl" else ["clang-repl"]
        else:
            aliases = [name]
        entries.append((name, path, aliases, "REPL JIT"))

    system_zig, bundled_zig = _find_all_zig()
    if system_zig:
        entries.append(("zig c++", system_zig, ["zig"], "system"))
    if bundled_zig:
        aliases = ["zig", "zig-bundled"] if not system_zig else ["zig-bundled"]
        entries.append(("zig c++", bundled_zig, aliases, "bundled"))

    if not entries:
        print("No C++ compilers found.")
        print("Install g++, clang++, or: uv tool install \"tpy-lang[bundled]\"")
        return

    name_width = max(len(b) for b, _, _, _ in entries)
    print("Available C++ compilers:")
    for binary, path, aliases, note in entries:
        marker = "*" if binary == auto_display else " "
        parts = []
        if aliases:
            parts.append("--cxx " + ", ".join(aliases))
        if note:
            parts.append(f"({note})")
        detail = "  ".join(parts)
        print(f"  {marker} {binary:<{name_width}}  {detail}")
    print(f"\n  auto selects: {auto_display}")
    print("  A path to any C++ compiler binary is also accepted.")


def discover_runtime_cpp_sources(runtime_cpp_dir: Path) -> list[Path]:
    """List TPy-owned .cpp files under `runtime_cpp_dir/src/` (recursively).

    These are compiled and linked into every TPy binary alongside user-
    generated .cpp files. They exist for stdlib helpers that need direct
    access to C system struct layouts (e.g. sockets / getaddrinfo) and
    can't be expressed header-only without leaking system-header macros
    into downstream TUs. Returns [] if the directory doesn't exist.

    `*_shim.*` files are third-party glue owned by a managed lib
    (mbedtls_shim.c, date_shim.cpp): they include vendored headers, so
    they are compiled by their lib's build factory (tpyc/build/<lib>.py)
    with the lib's flags, only when a module declares the dep -- never
    linked into every binary. (.c files are skipped by the glob anyway;
    the explicit filter keeps the .cpp shims out too.)
    """
    src_dir = runtime_cpp_dir / "src"
    if not src_dir.is_dir():
        return []
    return sorted(p for p in src_dir.rglob("*.cpp")
                  if not p.stem.endswith("_shim"))


def get_or_build_pch(
    config: CppCompilerConfig,
    runtime_include_dir: Path,
    opt_flags: list[str],
    pch_dir: Path,
) -> Path | None:
    """Return path to cached PCH header (with .gch next to it), or None on failure.

    The PCH is stored in pch_dir (typically inside the build output directory).
    Rebuilds only when runtime headers are newer than the cached .gch.
    """
    pch_dir.mkdir(parents=True, exist_ok=True)
    pch_header = pch_dir / "tpy_pch.hpp"
    pch_gch = pch_dir / "tpy_pch.hpp.gch"

    # Staleness check
    if pch_gch.exists():
        pch_mtime = pch_gch.stat().st_mtime
        runtime_tpy = runtime_include_dir / "tpy"
        stale = any(h.stat().st_mtime > pch_mtime
                    for h in runtime_tpy.glob("**/*.hpp"))
        if not stale:
            return pch_header

    pch_header.write_text('#include <tpy/tpy.hpp>\n')
    cmd = [
        *config.compiler, f"-std={config.std}",
        *config.extra_flags,
        *config.warn_flags,
        *opt_flags,
        "-I", str(runtime_include_dir),
        "-x", "c++-header",
        str(pch_header), "-o", str(pch_gch),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return pch_header
    return None


#: Recommended strict warning set for compiling tpy-generated code.
#: Mirrors the minimal subset we know downstream consumers run with -Werror.
#: The test suite turns these on (see tests/conftest.py); end-user CLI builds
#: do NOT enable them by default -- it's the consumer's choice whether to opt
#: in by feeding them via CXXFLAGS or a build-system flag.
#:
#: Notable choices:
#: - -Wno-unused-parameter: downstream suppresses; we may want to enable later
#:   if we want to be stricter than downstream (revisit if we add a sema pass
#:   that flags unread parameters at the source level).
#: - -Wshadow: NOT in this set. Record/dataclass constructors emit the
#:   idiomatic `T(P p) : p(p) {}` pattern, which always shadows. Downstream
#:   consumers compiling tpy-generated TUs should add -Wno-shadow per-TU.
#:
#: Family-specific suffixes follow: GCC-only and Clang-only flags split out
#: because each toolchain spells some warnings differently and emits noise
#: on patterns the other accepts. `strict_warn_flags(cxx)` returns the right
#: combined list given a compiler command.
_COMMON_WARN_FLAGS: list[str] = [
    "-Werror",
    "-Wall", "-Wextra",
    "-Wno-missing-field-initializers",
    "-Wno-unused-parameter",
    # Generated codegen artifacts that we accept as costing more than they
    # would be worth fixing piecemeal. Each has a different rationale -- see
    # the inventory in PR notes.
    "-Wno-unused-label",             # for/else and with/finally end labels
    "-Wno-unused-but-set-variable",  # tuple-unpack and error_return temps
    "-Wno-unused-but-set-parameter", # by-value params written via v.field = X but never read
    # User TPy code can write `x = compute()` followed by no read -- which
    # is silently allowed in Python. Per the "users never see C++ errors"
    # invariant we suppress at the C++ level. The user-facing signal lives
    # at the TPy layer; see TODO.md for a planned sema diagnostic.
    "-Wno-unused-variable",
    # Class-hygiene checks: zero hits today; cheap future-proofing.
    "-Wsign-compare",
    "-Wnon-virtual-dtor", "-Woverloaded-virtual",
    "-Wswitch-bool", "-Wsizeof-array-argument",
    "-Wsuggest-override",
    # Container indexing and other int32 / size_t crossings: codegen emits
    # explicit static_cast<size_t> at bounds-safe subscript sites and at
    # comprehension reserve / array-comp index sites; runtime headers cast
    # at audited internal boundaries.
    "-Wsign-conversion", "-Wconversion",
]

#: GCC-only diagnostics we want enabled. Apple clang rejects these as
#: -Wunknown-warning-option under -Werror.
_GCC_ONLY_WARN_FLAGS: list[str] = [
    "-Wbool-compare",
]

#: Clang flags codegen patterns that GCC silently accepts. Suppressing them
#: lets macOS dev builds match the GCC CI build instead of failing on noise.
#: TODO: revisit and either fix the codegen or split these into "real bugs"
#: and "harmless idioms".
_CLANG_ONLY_WARN_FLAGS: list[str] = [
    "-Wno-parentheses-equality",        # `if ((x == y))`: codegen wraps comparisons in parens
    "-Wno-pessimizing-move",            # `std::move(temp)` at construction sites
    "-Wno-shorten-64-to-32",            # std::size_t -> int32_t at varargs / comprehension boundaries
    "-Wno-tautological-overlap-compare",
    "-Wno-unused-lambda-capture",
    "-Wno-dangling-gsl",
    "-Wno-defaulted-function-deleted",
    "-Wno-float-conversion",            # implicit double -> bool in `if x` for float locals
    "-Wno-unused-value",                # `abs(0);` discards a const-attribute return
    "-Wno-self-assign",                 # `x = x` (legal Python no-op) lowers verbatim
    "-Wno-self-assign-field",           # `self.x = self.x` field self-assign, ditto
]


@functools.lru_cache(maxsize=8)
def _detect_compiler_family(cxx: tuple[str, ...]) -> str:
    """Returns 'gcc', 'clang', or 'unknown' by probing the compiler.

    Apple distributes their clang under the `g++` name, so basename matching
    misclassifies it. Running `--version` disambiguates reliably.
    """
    try:
        out = subprocess.run(
            list(cxx) + ["--version"],
            capture_output=True, text=True, timeout=5,
        ).stdout.lower()
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    if "clang" in out:
        return "clang"
    if "free software foundation" in out or "gcc" in out:
        return "gcc"
    return "unknown"


def strict_warn_flags(cxx: list[str]) -> list[str]:
    """Strict warning set tailored to the C++ compiler family.

    Apple ships their clang as `g++`, so name-based detection is unreliable;
    `_detect_compiler_family` runs `--version` to disambiguate.
    """
    family = _detect_compiler_family(tuple(cxx))
    if family == "gcc":
        return _COMMON_WARN_FLAGS + _GCC_ONLY_WARN_FLAGS
    if family == "clang":
        return _COMMON_WARN_FLAGS + _CLANG_ONLY_WARN_FLAGS
    return list(_COMMON_WARN_FLAGS)


@dataclass
class CppCompilerConfig:
    """Configuration for the C++ compiler used to build generated code."""
    compiler: list[str] = field(default_factory=lambda: ["g++"])
    std: str = "c++23"
    extra_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    ccache: bool = False
    # End-user CLI builds default to no extra warnings. Tests override this to
    # `strict_warn_flags(compiler)` so we catch generated-code regressions.
    warn_flags: list[str] = field(default_factory=list)

    @property
    def compiler_name(self) -> str:
        """Display name for the compiler (e.g. 'g++', 'zig c++')."""
        parts = [os.path.basename(self.compiler[0])] + self.compiler[1:]
        return " ".join(parts)

    @classmethod
    def from_env(cls, cxx: str = "auto") -> CppCompilerConfig:
        """Create config from --cxx flag value, CXX env var, or auto-detection.

        Resolution order:
        1. Explicit --cxx value (if not "auto")
        2. CXX environment variable (if set)
        3. Auto-detect: g++ > clang++ > zig c++
        """
        if cxx != "auto":
            resolved = _resolve_compiler(cxx)
            if resolved is None:
                raise CompilerNotFoundError(cxx)
            ccache = not _is_zig(resolved) and shutil.which("ccache") is not None
            return cls(compiler=resolved, ccache=ccache)

        env_cxx = os.environ.get("CXX", "")
        if env_cxx:
            compiler = env_cxx.split()
            ccache = not _is_zig(compiler) and shutil.which("ccache") is not None
            return cls(compiler=compiler, ccache=ccache)

        compiler = _auto_detect_compiler()
        ccache = not _is_zig(compiler) and shutil.which("ccache") is not None
        return cls(compiler=compiler, ccache=ccache)
