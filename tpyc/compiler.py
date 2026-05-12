"""
TurboPython Multi-Module Compiler

Orchestrates compilation of multiple modules, handling:
- Module discovery and dependency resolution
- Circular import detection
- Compilation in dependency order
- Export tracking for cross-module references
"""

from __future__ import annotations
import functools
import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, TYPE_CHECKING

from .parse import Parser, ParseError, TpyModule, TpyImport, TpyVarDecl, RelativeImportKey, SourceLocation
from .parse.imports import _IMPLICIT_MODULES, _PRIVATE_MODULE_PUBLIC_NAMES, route_stdlib_name
from .module_names import public_module_name as _public_module_name_of
from .sema import SemanticAnalyzer, SemanticError, Diagnostic, DiagnosticLevel
from .sema.reach_analysis import compute_reached_symbols
from .modules.resolver import ModuleResolver, ResolvedModule
from .modules import get_builtin_module_names
from .type_def_registry import (
    get_type_def as _get_type_def,
    attach_dynamic_type_def, TypeCategory,
)
from .codegen_cpp import CodeGenerator, CodeGenOptions
from .codegen_cpp.context import qualified_cpp_name, set_namespace_map, set_include_path_map, get_include_path, clear_namespace_map, module_to_include_path
from .typesys import (
    TpyType, INT32, INT64, BIGINT, VOID, NominalType,
    RecordInfo, FunctionInfo, ProtocolInfo,
    OwnType, OptionalType, UnionType, TupleType, PtrType, RefType, ReadonlyType,
    is_fn_type, unwrap_ref_type, is_protocol_type, is_protocol_union,
    clear_all_compilation_state,
)
from .type_def_registry import protocol_info_of
from .module_names import public_module_name
from .macro_loader import MacroRegistry, is_macro_module_source
from .symbol_binding import (
    BindingCell, SymbolKind, install_binding, protocol_kind_for,
)

if TYPE_CHECKING:
    from .typesys import ModuleInfo, ModuleVarInfo


DEFAULT_INT_CHOICES = ("Int32", "Int64", "BigInt")

# Maps user-facing platform names in # tpy: link() to sys.platform prefixes
_PLATFORM_MAP = {"windows": "win32", "linux": "linux", "macos": "darwin"}


def _has_static_protocol_param(params) -> bool:
    """Mirror codegen's static-protocol-param detection (see
    `tpyc.codegen_cpp.protocols.ProtocolGenerator.get_all_protocol_params`)
    using sema-level type predicates only. A static protocol param
    forces the function to be emitted as a C++ template; a @dynamic
    protocol param does not. Used by the completeness-graph reject
    gate to recognize the same set of "in-header-bodied" functions
    as codegen.
    """
    for _, ptype in params:
        unwrapped = ptype
        if isinstance(unwrapped, RefType):
            unwrapped = unwrapped.wrapped
        if isinstance(unwrapped, ReadonlyType):
            unwrapped = unwrapped.wrapped
        if isinstance(unwrapped, OwnType):
            unwrapped = unwrapped.wrapped
        if isinstance(unwrapped, OptionalType):
            inner = unwrapped.inner
            if is_protocol_type(inner) and isinstance(inner, NominalType):
                pi = protocol_info_of(inner)
                if pi is None or not pi.is_dynamic:
                    return True
            continue
        if isinstance(unwrapped, UnionType) and is_protocol_union(unwrapped):
            for m in unwrapped.members:
                if isinstance(m, NominalType) and is_protocol_type(m):
                    pi = protocol_info_of(m)
                    if pi is None or not pi.is_dynamic:
                        return True
            continue
        if is_protocol_type(unwrapped) and isinstance(unwrapped, NominalType):
            pi = protocol_info_of(unwrapped)
            if pi is None or not pi.is_dynamic:
                return True
    return False


def _is_template_emitted_in_header(func) -> bool:
    """True when codegen will emit the function's *definition* in the
    .hpp rather than .cpp -- the gate-relevant predicate for
    cycle-member functions and methods. Mirrors
    `FunctionGenerator.is_template_function` plus @cpp_template stubs.
    """
    if func.type_params:
        return True
    if getattr(func, "cpp_template", None) is not None:
        return True
    if any(is_fn_type(unwrap_ref_type(p[1])) for p in func.params):
        return True
    if _has_static_protocol_param(func.params):
        return True
    return False


def parse_default_int_type(name: str) -> TpyType:
    """Parse CLI/compiler default-int setting into a concrete semantic type."""
    if name == "Int32":
        return INT32
    if name == "Int64":
        return INT64
    if name == "BigInt":
        return BIGINT
    raise ValueError(
        f"Unsupported default int type '{name}'. Expected one of: "
        f"{', '.join(DEFAULT_INT_CHOICES)}"
    )


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
        print("Install g++, clang++, or: uv tool install \"tpy-poc[bundled]\"")
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
    """
    src_dir = runtime_cpp_dir / "src"
    if not src_dir.is_dir():
        return []
    return sorted(src_dir.rglob("*.cpp"))


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


class BuildLayout:
    """Manages the build directory structure for compiled modules.

    Layout:
        output_dir/
          {entry_module}.d/        # root_dir (flat=False) or output_dir/ (flat=True)
            include/               # headers (shared across variants)
              {module}.hpp
              {pkg}/{mod}.hpp
            src/                   # sources (shared across variants)
              {module}.cpp
              {pkg}/{mod}.cpp
            runtime/               # bundled tpy runtime headers (when bundle_runtime=True)
              include/
                tpy/
            debug/                 # variant-specific build artifacts
              {entry_module}.o
              {entry_module}
            release/
              {entry_module}.o
              {entry_module}
    """

    def __init__(self, output_dir: Path, entry_module_name: str,
                 build_variant: str | None = None, flat: bool = False):
        self.output_dir = output_dir
        self.entry_module_name = entry_module_name
        self.root_dir = output_dir if flat else output_dir / f"{entry_module_name}.d"
        self.include_dir = self.root_dir / "include"
        self.src_dir = self.root_dir / "src"
        self.build_variant = build_variant
        self.build_dir = self.root_dir / build_variant if build_variant else self.root_dir

    def hpp_path(self, module_name: str) -> Path:
        """Path to the generated header for a module."""
        return self._module_path(module_name, ".hpp", self.include_dir)

    def fwd_hpp_path(self, module_name: str) -> Path:
        """Path to the cycle-member forward-declaration header
        (`<mod>_fwd.hpp`). Cycle peers include this instead of the
        full header to break the complete-type cycle for pointer /
        reference positions.
        """
        return self._module_path(module_name, "_fwd.hpp", self.include_dir)

    def cpp_path(self, module_name: str) -> Path:
        """Path to the generated source for a module."""
        return self._module_path(module_name, ".cpp", self.src_dir)

    @staticmethod
    def _module_path(module_name: str, suffix: str, base_dir: Path) -> Path:
        """Build a file path for `module_name` under `base_dir`.

        Honors the `# tpy: include()` override (which gives a `.hpp`
        path; we strip and re-suffix). Otherwise dotted names map to
        nested directories with the leaf name as the file stem.
        `suffix` is the trailing component (".hpp", ".cpp",
        "_fwd.hpp", ...).
        """
        override = get_include_path(module_name)
        if override is not None:
            return base_dir / (override.removesuffix('.hpp') + suffix)
        parts = module_name.split('.')
        leaf = f"{parts[-1]}{suffix}"
        if len(parts) == 1:
            return base_dir / leaf
        return base_dir / Path(*parts[:-1]) / leaf

    def binary_path(self) -> Path:
        """Path to the output binary."""
        return self.build_dir / self.entry_module_name

    def build_cpp_commands(
        self,
        runtime_include_dir: Path,
        cpp_files: list[Path],
        output: Path | None = None,
        opt_flags: list[str] | None = None,
        config: CppCompilerConfig | None = None,
        extra_objects: list[str] | None = None,
        extra_include_dirs: list[Path] | None = None,
        force_includes: list[Path] | None = None,
        extra_link_flags: list[str] | None = None,
        c_sources: list[tuple[Path, list[str]]] | None = None,
        runtime_cpp_sources: list[Path] | None = None,
    ) -> list[list[str]]:
        """Build C++ compilation commands: per-file compile steps + a link step.

        Always splits into per-file -c steps and a final link, enabling
        parallel compilation and per-file ccache caching. The last element
        of the returned list is always the link step.

        Args:
            runtime_include_dir: Path to the tpy runtime include directory.
            cpp_files: List of C++ source files to compile.
            output: Output binary path. Defaults to self.binary_path().
            opt_flags: Optimization flags (e.g., ["-O3", "-DNDEBUG"]).
            config: Compiler configuration. Defaults to CppCompilerConfig().
            extra_objects: Pre-compiled object files to include in the link step.
            extra_include_dirs: Additional include directories (-I flags).
            force_includes: Headers to force-include via -include flag.
            extra_link_flags: Additional link-line flags (e.g. -lpcre2-8 from
                              third-party deps in system mode).
            c_sources: Bundled C source files to compile with the C compiler
                       (derived from config.compiler), each paired with its
                       own list of compile flags. Used to build vendored C
                       deps like PCRE2 inline with the user binary.
            runtime_cpp_sources: TPy-owned C++ runtime sources (typically
                       discovered under runtime/cpp/src/ via
                       `discover_runtime_cpp_sources`). Compiled with the
                       same C++ toolchain + include paths as generated user
                       code; .o files go into a `runtime/` subdir of the
                       build dir (prefix avoids stem collisions with user
                       modules of the same name).
        """
        if config is None:
            config = CppCompilerConfig()
        if output is None:
            output = self.binary_path()

        extra_flags: list[str] = []
        for d in (extra_include_dirs or []):
            extra_flags += ["-I", str(d)]
        for h in (force_includes or []):
            extra_flags += ["-include", str(h)]

        common = [
            *config.compiler, f"-std={config.std}",
            *(opt_flags or []),
            *config.extra_flags,
            *config.warn_flags,
            "-I", str(runtime_include_dir),
            "-I", str(self.include_dir),
            *extra_flags,
        ]

        self.build_dir.mkdir(parents=True, exist_ok=True)

        # Always split: compile each .cpp -> .o, then link .o files.
        # This enables parallel compilation and per-file ccache caching.
        obj_files: list[str] = []
        cmds: list[list[str]] = []
        for cpp in cpp_files:
            # Mangle path to avoid .o collisions for package modules
            try:
                rel = cpp.relative_to(self.src_dir)
                obj_name = str(rel).replace(os.sep, "_").removesuffix(".cpp") + ".o"
            except ValueError:
                obj_name = cpp.stem + ".o"
            obj = self.build_dir / obj_name
            obj_files.append(str(obj))
            prefix = ["ccache"] if config.ccache else []
            cmds.append([
                *prefix, *common,
                "-c", "-o", str(obj), str(cpp),
            ])

        # Bundled C source files (e.g. PCRE2 in bundled mode). Compiled with
        # the C compiler derived from config.compiler -- C++ drivers reject
        # implicit void* conversions that PCRE2 relies on.
        if c_sources:
            c_compiler = _derive_c_compiler(config.compiler)
            third_party_dir = self.build_dir / "third_party"
            third_party_dir.mkdir(parents=True, exist_ok=True)
            for c_file, c_extra_flags in c_sources:
                obj = third_party_dir / (c_file.stem + ".o")
                obj_files.append(str(obj))
                prefix = ["ccache"] if config.ccache else []
                cmds.append([
                    *prefix, *c_compiler,
                    *(opt_flags or []),
                    *c_extra_flags,
                    "-c", "-o", str(obj), str(c_file),
                ])

        # TPy-owned runtime .cpp files. Same C++ toolchain + include paths
        # as user code. Object names derive from the path relative to
        # runtime/cpp/src/ to avoid collisions with user modules whose
        # .cpp happens to share a stem.
        if runtime_cpp_sources:
            rt_src_root = runtime_include_dir.parent / "src"
            rt_obj_dir = self.build_dir / "runtime"
            rt_obj_dir.mkdir(parents=True, exist_ok=True)
            for rt_cpp in runtime_cpp_sources:
                try:
                    rel = rt_cpp.relative_to(rt_src_root)
                    obj_name = "rt_" + str(rel).replace(os.sep, "_").removesuffix(".cpp") + ".o"
                except ValueError:
                    obj_name = "rt_" + rt_cpp.stem + ".o"
                obj = rt_obj_dir / obj_name
                obj_files.append(str(obj))
                prefix = ["ccache"] if config.ccache else []
                cmds.append([
                    *prefix, *common,
                    "-c", "-o", str(obj), str(rt_cpp),
                ])

        cmds.append([
            *config.compiler,
            "-o", str(output),
            *obj_files,
            *(extra_objects or []),
            *config.link_flags,
            *(extra_link_flags or []),
        ])
        return cmds

    def generate_cmake(
        self,
        runtime_include_dir: Path,
        cpp_files: list[Path],
        link_flags: list[str] | None = None,
        extra_include_dirs: list[Path] | None = None,
        bundle_runtime: bool = True,
        third_party_libs: list[Any] | None = None,
        runtime_cpp_sources: list[Path] | None = None,
    ) -> Path:
        """Generate a .cmake include file with source/include/link variables.

        Produces sources.cmake that sets:
          TPYC_SOURCES      -- list of generated .cpp files
          TPYC_INCLUDE_DIRS -- include directories (generated headers + runtime)
          TPYC_LIBRARIES    -- link libraries (from # tpy: link() directives,
                               extended at configure time by third-party
                               selectors)
          TPYC_CXX_STANDARD -- required C++ standard (23)

        When bundle_runtime is True (default), the runtime headers are copied
        into the output directory so the result is self-contained.

        third_party_libs (list of `tpyc.build.third_party.ThirdPartyLib`)
        gets the 3-mode CMake selector emitted for each. When bundle_runtime
        is True, vendored source trees are also copied alongside the runtime
        so the output is fully self-contained.

        Users include() this from their CMakeLists.txt.
        """
        cmake_path = self.root_dir / "sources.cmake"
        cmake_dir = "${CMAKE_CURRENT_LIST_DIR}"

        sources = []
        for cpp in cpp_files:
            try:
                sources.append(f"{cmake_dir}/{cpp.relative_to(self.root_dir)}")
            except ValueError:
                sources.append(str(cpp))

        include_dirs = []
        include_dirs.append(f"{cmake_dir}/{self.include_dir.relative_to(self.root_dir)}")

        runtime_cpp_dir = runtime_include_dir.parent  # .../runtime/cpp
        runtime_src_dir = runtime_cpp_dir / "src"

        if bundle_runtime:
            bundled_dir = self.root_dir / "runtime" / "include"
            if bundled_dir.exists():
                shutil.rmtree(bundled_dir)
            shutil.copytree(runtime_include_dir, bundled_dir)
            include_dirs.append(f"{cmake_dir}/runtime/include")
            # Bundle runtime sources too so the sources.cmake is self-contained
            # (references paths under `runtime/src/` rather than reaching back
            # into the original toolchain checkout).
            if runtime_cpp_sources and runtime_src_dir.is_dir():
                bundled_src = self.root_dir / "runtime" / "src"
                if bundled_src.exists():
                    shutil.rmtree(bundled_src)
                shutil.copytree(runtime_src_dir, bundled_src)
                for rt_cpp in runtime_cpp_sources:
                    try:
                        rel = rt_cpp.relative_to(runtime_src_dir)
                        sources.append(f"{cmake_dir}/runtime/src/{rel}")
                    except ValueError:
                        sources.append(str(rt_cpp))
        else:
            try:
                include_dirs.append(f"{cmake_dir}/{os.path.relpath(runtime_include_dir, self.root_dir)}")
            except ValueError:
                include_dirs.append(str(runtime_include_dir))
            for rt_cpp in (runtime_cpp_sources or []):
                try:
                    sources.append(f"{cmake_dir}/{os.path.relpath(rt_cpp, self.root_dir)}")
                except ValueError:
                    sources.append(str(rt_cpp))

        for extra_d in (extra_include_dirs or []):
            try:
                include_dirs.append(f"{cmake_dir}/{os.path.relpath(extra_d, self.root_dir)}")
            except ValueError:
                include_dirs.append(str(extra_d))

        libraries = []
        for flag in (link_flags or []):
            if flag.startswith("-l"):
                libraries.append(flag[2:])

        lines = [
            "# Generated by tpyc -- include() this from your CMakeLists.txt",
            "#",
            "# Example usage:",
            "#   include(path/to/sources.cmake)",
            "#   add_executable(myapp ${TPYC_SOURCES})",
            "#   target_include_directories(myapp PRIVATE ${TPYC_INCLUDE_DIRS})",
            "#   target_link_libraries(myapp PRIVATE ${TPYC_LIBRARIES})",
            "#   set_target_properties(myapp PROPERTIES CXX_STANDARD ${TPYC_CXX_STANDARD})",
            "",
            "set(TPYC_SOURCES",
        ]
        for src in sources:
            lines.append(f"    {src}")
        lines.append(")")
        lines.append("")

        lines.append("set(TPYC_INCLUDE_DIRS")
        for d in include_dirs:
            lines.append(f"    {d}")
        lines.append(")")
        lines.append("")

        lines.append("set(TPYC_LIBRARIES")
        for lib in libraries:
            lines.append(f"    {lib}")
        lines.append(")")
        lines.append("")

        # Third-party libraries (PCRE2 etc.). Each lib emits a 3-mode
        # selector that appends to TPYC_LIBRARIES and, in bundled mode,
        # add_subdirectory()s the vendored copy.
        if third_party_libs:
            from .build.third_party import (
                emit_cmake_snippet, bundle_source_tree, collect_licenses,
            )
            for lib in third_party_libs:
                lines.append(emit_cmake_snippet(lib))
                lines.append("")
                if bundle_runtime:
                    bundle_source_tree(lib, self.root_dir)
            if bundle_runtime:
                collect_licenses(third_party_libs, self.root_dir)

        lines.append("set(TPYC_CXX_STANDARD 23)")
        lines.append("")

        cmake_path.parent.mkdir(parents=True, exist_ok=True)
        cmake_path.write_text("\n".join(lines) + "\n")
        return cmake_path


@dataclass
class ModuleExports:
    """Exports from a compiled module.

    Contains all items that can be imported from this module:
    - functions: dict of function name -> list of FunctionInfo (overloads)
    - records: dict of record name -> RecordInfo
    - protocols: dict of protocol name -> ProtocolInfo
    - variables: dict of variable name -> TpyType
    - reexported_functions: dict of local name -> (source_module, original_name) for re-exports
    - reexported_records: dict of local name -> (source_module, original_name) for re-exports
    - reexported_variables: dict of local name -> (source_module, original_name) for re-exports
    """
    functions: dict[str, list[FunctionInfo]] = field(default_factory=dict)
    records: dict[str, RecordInfo] = field(default_factory=dict)
    protocols: dict[str, ProtocolInfo] = field(default_factory=dict)
    enums: dict[str, 'NominalType'] = field(default_factory=dict)
    variables: dict[str, TpyType] = field(default_factory=dict)
    type_aliases: dict[str, TpyType] = field(default_factory=dict)
    # Subset of type_aliases that are recursive union aliases (compile to a
    # C++ wrapper struct). Cross-module consumers need this to qualify the
    # alias name with the defining module's namespace.
    recursive_union_names: set[str] = field(default_factory=set)
    reexported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_enums: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Names of variables declared Final[T] in this module. Kept separate from
    # `variables` because FinalType is stripped at sema registration time.
    final_variables: set[str] = field(default_factory=set)
    # Defining modules this module's generated code references. Populated
    # by sema.reach_analysis after analysis; threaded into ModuleInfo.reached.
    reached: set[str] = field(default_factory=set)


class _SkeletonSnapshot(NamedTuple):
    """Identity snapshot of a module's pre-populated skeleton sema
    objects. Recorded by `_pre_populate_decl_exports`, consumed and
    then cleared by `_verify_skeleton_adoption` at the end of
    `_finalize_declarations`. See `Compiler._verify_skeleton_adoption`
    for the invariant being checked.
    """
    records: dict[str, int]               # name -> id(RecordInfo)
    protocols: dict[str, int]             # name -> id(ProtocolInfo)
    functions: dict[str, tuple[int, int]] # name -> (id(list), id(list[0]))


@dataclass
class CompiledModule:
    """A compiled module with its AST and exports."""
    name: str
    path: Path
    ast: TpyModule
    exports: ModuleExports
    is_entry_point: bool
    is_package_init: bool = False
    analyzer: SemanticAnalyzer | None = None
    # Per-module attribute table: short local name -> BindingCell.
    # Phase 1 of the per-module attribute table refactor. Populated
    # incrementally during _pre_populate_decl_exports (locals),
    # _register_user_module_import (imports), register_globals
    # (variables), and type-alias / re-export extraction. No readers
    # in Phase 1; debug assertions cross-check against the per-kind
    # dicts on `exports`. See docs/MUTUAL_IMPORTS_DESIGN.md.
    module_attributes: dict[str, BindingCell] = field(default_factory=dict)
    # Set by `_pre_populate_decl_exports`, cleared by
    # `_verify_skeleton_adoption` once finalization completes; lives
    # on the module only for the brief sema-phase window between the
    # two calls.
    _skeleton_ids: _SkeletonSnapshot | None = None


class CompileError(Exception):
    """Error during multi-module compilation."""
    def __init__(self, message: str, module_name: str | None = None, path: Path | None = None,
                 lineno: int | None = None):
        self.message = message
        self.module_name = module_name
        self.path = path
        self.lineno = lineno
        super().__init__(message)

    def format(self) -> str:
        """Format error with module context."""
        if self.path:
            if self.lineno:
                return f"{self.path.name}:{self.lineno}: error: {self.message}"
            return f"{self.path.name}: error: {self.message}"
        if self.module_name:
            return f"module '{self.module_name}': error: {self.message}"
        return f"error: {self.message}"


class Compiler:
    """Multi-module compiler for TurboPython.

    Handles module discovery, dependency resolution, and compilation order.
    """

    def __init__(self, entry_point: Path, default_int: str = "Int32",
                 lib_dirs: list[Path] | None = None):
        """Initialize compiler with entry point path.

        Args:
            entry_point: Path to the main source file.
            default_int: Unannotated integer literal default type.
            lib_dirs: Extra directories to search for library modules.
        """
        self.entry_point = entry_point.resolve()
        self.resolver: ModuleResolver | None = ModuleResolver(self.entry_point.parent,
                                                               extra_dirs=lib_dirs or [])
        self.default_int_type = parse_default_int_type(default_int)
        self._init_shared()

    def _init_shared(self) -> None:
        """Initialize state shared by both file and stdin compilation paths."""
        self.modules: dict[str, CompiledModule] = {}
        # Workspace-wide ModuleInfo dict aliased into every analyzer's
        # TypeRegistry.modules. See SemanticAnalyzer.__init__ for the
        # ownership rule: per-module short-name bindings stay
        # per-analyzer; only the qname-keyed `modules` surface is shared
        # so cross-module lookups see every peer's contribution.
        from .typesys import ModuleInfo as _ModuleInfo
        self._shared_modules: dict[str, _ModuleInfo] = {}
        # Per-module map of cycle peers (populated by
        # `_compute_compile_order` from Tarjan SCC output). Codegen
        # consults this to swap full <peer>.hpp includes for
        # <peer>_fwd.hpp in the header path.
        self._cycle_peers: dict[str, frozenset[str]] = {}
        self.compile_order: list[str] = []
        self.shadowed_builtins: dict[str, set[tuple[str, int | None]]] = {}
        self.diagnostics: list[Diagnostic] = []
        self._source_input: tuple[str, str] | None = None
        search_dirs = []
        if self.resolver is not None:
            search_dirs = [self.resolver.base_dir] + self.resolver.extra_dirs
        self._macro_registry = MacroRegistry(search_dirs=search_dirs)
        # Decorator arg schemas derived from @builtin_decorator stubs,
        # accumulated across parsed modules and passed to subsequent parsers.
        self._decorator_schemas: dict = {}
        # REPL mode: allow @error_return calls at top level (unwrap with panic)
        self.allow_top_level_error_unwrap: bool = False
        # Records whose builtin self-refs have already been resolved (perf cache).
        # Keyed by record object identity: _exports_to_module_info passes
        # exports.records by reference, so the same RecordInfo appears in
        # multiple ModuleInfo.records dicts. id() reuse is not a concern
        # because RecordInfo objects live in Compiler.modules for the full
        # compiler lifetime.
        self._resolved_self_ref_records: set[int] = set()
        # Public-surface -> raw-private-submodule reverse map used by
        # `_canonicalize_import_sources` when a dependent's surface
        # module (e.g. `typing`) is not yet sema-analyzed but its
        # private backing module is.  Populated lazily on first call
        # and invalidated by re-entering `_init_shared`.
        self._public_to_raw_cache: dict[str, list[str]] | None = None

    @classmethod
    def from_source(
        cls,
        source: str,
        module_name: str = "main",
        default_int: str = "Int32",
        lib_dirs: list[Path] | None = None,
    ) -> "Compiler":
        """Create a compiler for a single module from source code (e.g., stdin)."""
        compiler = cls.__new__(cls)
        compiler.entry_point = Path("<stdin>")
        if lib_dirs:
            compiler.resolver = ModuleResolver(Path.cwd(), extra_dirs=lib_dirs)
        else:
            compiler.resolver = None
        compiler.default_int_type = parse_default_int_type(default_int)
        compiler._init_shared()
        compiler._source_input = (source, module_name)
        return compiler

    def compile(self) -> list[CompiledModule]:
        """Compile entry point and all imported modules.

        Returns:
            List of CompiledModule in dependency order (dependencies first).

        Raises:
            CompileError: If circular imports detected or module not found.
            ParseError: If parsing fails.
            SemanticError: If semantic analysis fails.
        """
        clear_all_compilation_state()
        clear_namespace_map()  # separate from clear_all_compilation_state (lives in codegen)

        if self._source_input is not None:
            source, entry_name = self._source_input

            if self.resolver:
                # Discover implicit stdlib first so @builtin_decorator schemas
                # are available when parsing the entry module.
                self._discover_implicit_stdlib()

            parser = Parser(decorator_schemas=self._decorator_schemas)
            ast = parser.parse(source, module_name=entry_name, is_entry_point=True)
            self._decorator_schemas.update(parser._decorator_schemas)
            self.modules[entry_name] = CompiledModule(
                name=entry_name,
                path=Path("<stdin>"),
                ast=ast,
                exports=ModuleExports(),
                is_entry_point=True,
            )

            if self.resolver:
                # Discover imported library modules
                self._discover_imports(entry_name, ast, [entry_name])
                self._discover_implicit_stdlib()
                self._compute_compile_order()
                self._propagate_package_directives()
            else:
                self.compile_order = [entry_name]

            self._apply_native_namespace_prefix()
            ns_map = self._build_namespace_map()
            set_namespace_map(ns_map)
            set_include_path_map(self._build_include_path_map(ns_map))
            # Workspace-wide two-pass sema. Pre-populate compiled.exports
            # with skeletons for cross-module bind_imports to find;
            # then resolve types per module; then finalize decls per
            # module; then bodies per module.
            for name in self.compile_order:
                self._pre_populate_decl_exports(self.modules[name])
            # Second pre-pop pass: install re-export bindings (PLACEHOLDER
            # cells refined to RESOLVED via chain walk against peers'
            # already-pre-populated locals). Required for cycle members
            # whose bind_imports runs before a peer's bind_imports has
            # had a chance to materialize re-exported names. Iterate
            # to a fixpoint so SCC-internal chains converge.
            self._pre_populate_reexport_bindings()
            for name in self.compile_order:
                compiled = self.modules[name]
                self._expand_star_imports_for_module(compiled)
                self._resolve_types_for_module(compiled)
                self._finalize_declarations(compiled)
            # Phantom-name check runs as a separate post-loop pass so SCC
            # cycle peers' module_attributes is fully populated before any
            # peer's __all__ is diffed against its surface.
            for name in self.compile_order:
                self._check_module_all_completeness(self.modules[name])
            self._check_workspace_completeness_cycles()
            for name in self.compile_order:
                self._analyze_bodies(self.modules[name])
            for name in self.compile_order:
                analyzer = self.modules[name].analyzer
                if analyzer is not None:
                    analyzer.finalize_borrow_checks()
            return [self.modules[name] for name in self.compile_order]

        # 1. Discover implicit stdlib first so @builtin_decorator schemas
        # are available when parsing user modules.
        self._discover_implicit_stdlib()

        # 1b. Discover all modules (starting from entry point)
        # Entry point uses simple name (not dotted) since it's the root
        entry_name = ModuleResolver.get_module_name(self.entry_point)
        self._discover_modules(entry_name, self.entry_point, [], is_entry_point=True)

        # Rediscover in case user modules added new stdlib deps
        self._discover_implicit_stdlib()

        # 2. Compute compilation order (topological sort)
        self._compute_compile_order()

        # 3. Propagate native_module from package inits to child modules
        self._propagate_package_directives()

        # 3b. Apply native_namespace auto-prefix to bare @native entities
        self._apply_native_namespace_prefix()

        # 4. Build namespace and include path maps from # tpy: directives
        ns_map = self._build_namespace_map()
        set_namespace_map(ns_map)
        set_include_path_map(self._build_include_path_map(ns_map))

        # 5. Workspace-wide two-pass sema (cyclic-import support).
        # First pre-populate every module's `compiled.exports` with
        # skeleton RecordInfo / FunctionInfo / ProtocolInfo / enum
        # NominalType so peers' `bind_imports` can find a stable Python
        # object for each cross-module imported name -- including
        # between cycle members. Then resolve types + finalize decls per
        # module; then bodies per module. Canonicalization itself is
        # parser-level so it does not depend on order.
        for module_name in self.compile_order:
            self._pre_populate_decl_exports(self.modules[module_name])
        # Second pre-pop pass: install re-export bindings so cycle peers'
        # bind_imports finds names re-exported from other peers (whose
        # own bind_imports may not have run yet).
        self._pre_populate_reexport_bindings()
        for module_name in self.compile_order:
            compiled = self.modules[module_name]
            self._expand_star_imports_for_module(compiled)
            self._resolve_types_for_module(compiled)
            self._finalize_declarations(compiled)
        # Phantom-name check runs as a separate post-loop pass so SCC
        # cycle peers' module_attributes is fully populated before any
        # peer's __all__ is diffed against its surface.
        for module_name in self.compile_order:
            self._check_module_all_completeness(self.modules[module_name])
        # Completeness-graph reject gate.
        # Run after all decls are finalized so RecordInfo.fields / .parents
        # are populated. Scans cycle members for by-value cross-cycle
        # record references (concrete inheritance, by-value fields, value-
        # variant fields, by-value containers / tuples) and produces a
        # structured TPy diagnostic naming the offending positions and
        # modules. Without this, the cycle would compile through sema and
        # fail at the C++ build with a complete-type compiler error.
        self._check_workspace_completeness_cycles()
        for module_name in self.compile_order:
            self._analyze_bodies(self.modules[module_name])
        # Workspace-wide borrow-check resolution. Each module's pending
        # borrow checks are queued during body sema; resolving them
        # only after every module's propagation has completed
        # guarantees the resolution reads finalized cross-module
        # mutation facts regardless of body-sema iteration order.
        for module_name in self.compile_order:
            analyzer = self.modules[module_name].analyzer
            if analyzer is not None:
                analyzer.finalize_borrow_checks()

        # Return in dependency order
        return [self.modules[name] for name in self.compile_order]

    # Stdlib modules that are always compiled (even without explicit import).
    # These provide protocol definitions used by builtins (e.g. Sized for len()).
    _IMPLICIT_STDLIB = ["typing", "tpy", "builtins"]

    def _implicit_stdlib_set(self) -> set[str]:
        """Return the set of implicit stdlib modules including submodules."""
        return self._expand_implicit_prefixes(self.modules)

    @classmethod
    def _expand_implicit_prefixes(cls, names: Iterable[str]) -> set[str]:
        prefixes = tuple(f"{m}." for m in cls._IMPLICIT_STDLIB)
        return {m for m in names
                if m in cls._IMPLICIT_STDLIB or m.startswith(prefixes)}

    def is_user_module(self, compiled: CompiledModule) -> bool:
        """True if the module is user code (lives under the entry point directory)."""
        if not self.resolver:
            return True
        try:
            compiled.path.relative_to(self.resolver.base_dir)
            return True
        except ValueError:
            return False

    def _discover_implicit_stdlib(self) -> None:
        """Discover implicit stdlib modules that builtins depend on."""
        if not self.resolver:
            return
        for mod_name in self._IMPLICIT_STDLIB:
            if mod_name in self.modules:
                continue
            resolved = self.resolver.resolve(mod_name)
            if resolved is None:
                continue  # not available (e.g. --no-stdlib)
            self._discover_modules(resolved.canonical_name, resolved.path, [],
                                   is_package_init=resolved.is_package_init)

    def _discover_modules(self, module_name: str, path: Path, import_chain: list[str],
                          import_lineno: int | None = None, is_entry_point: bool = False,
                          is_package_init: bool = False) -> None:
        """Recursively discover modules starting from the given module.

        Args:
            module_name: Canonical name of the module (dotted for packages, e.g., "mypackage.submod").
            path: Path to the module file.
            import_chain: Current import chain for cycle detection.
            import_lineno: Line number of the import statement that triggered this discovery.
            is_entry_point: True if this is the entry point module.
            is_package_init: True if this is a package __init__ file.

        Raises:
            CompileError: If module not found.
        """
        # Cyclic imports are tolerated at discovery time -- the module
        # is already on the chain, recursing would loop forever, so
        # just bail. The conservatism gate (which cycle shapes are
        # supported) lives in `_reject_unsupported_cycle`, called
        # from `_compute_compile_order` once we know the SCC structure.
        if module_name in import_chain:
            return

        if module_name in self.modules:
            return

        # Check for macro module directive before parsing (macro modules
        # contain Python code that the TPy parser can't handle)
        source = path.read_text()
        if is_macro_module_source(source):
            self._macro_registry.load_module(module_name, path)
            # Discover MACRO_DEPS so they get compiled, and add them as
            # dependencies of the importing module for correct compile order.
            macro_deps = self._macro_registry.get_deps(module_name)
            if macro_deps:
                # The importing module (last in chain) needs the deps compiled first
                importer = import_chain[-1] if import_chain else None
                builtin_names = get_builtin_module_names()
                for dep in macro_deps:
                    # Skip modules that are already discovered (implicit stdlib)
                    if dep not in self.modules:
                        self._process_user_import(dep, None, module_name, path,
                                                  builtin_names,
                                                  import_chain + [module_name])
                    if importer and importer in self.modules:
                        importer_ast = self.modules[importer].ast
                        # Add dependency edge so topological sort orders correctly
                        importer_ast.user_module_imports.setdefault(dep, 0)
                        # Inject TpyImport so codegen emits __tpy_init() calls
                        # for macro dep modules (needed for their runtime globals).
                        if not any(isinstance(s, TpyImport) and s.module_name == dep
                                   for s in importer_ast.top_level_stmts):
                            importer_ast.top_level_stmts.insert(0, TpyImport(module_name=dep))
            return

        # Parse the module
        parser = Parser(decorator_schemas=self._decorator_schemas)
        try:
            ast = parser.parse(source, module_name=module_name,
                               is_package_init=is_package_init,
                               is_entry_point=is_entry_point)
        except ParseError as e:
            raise CompileError(e.message, module_name, path, lineno=e.lineno)
        self._decorator_schemas.update(parser._decorator_schemas)

        self.modules[module_name] = CompiledModule(
            name=module_name,
            path=path,
            ast=ast,
            exports=ModuleExports(),
            is_entry_point=is_entry_point,
            is_package_init=is_package_init,
        )

        # Recursively discover imported user modules
        new_chain = import_chain + [module_name]
        builtin_names = get_builtin_module_names()
        import_queue = list(ast.user_module_imports.items())
        processed = set()
        while import_queue:
            imported_name, import_lineno = import_queue.pop(0)
            if imported_name in processed:
                continue
            processed.add(imported_name)

            if RelativeImportKey.is_placeholder(imported_name):
                resolved_name = self._resolve_relative_import(
                    imported_name, import_lineno, module_name, path, ast, import_queue, is_package_init
                )
                if resolved_name is None:
                    continue
                imported_name = resolved_name

            self._process_user_import(imported_name, import_lineno, module_name, path, builtin_names, new_chain, parent_ast=ast)

    def _resolve_relative_import(
        self,
        imported_name: str,
        import_lineno: int,
        module_name: str,
        path: Path,
        ast: TpyModule,
        import_queue: list[tuple[str, int]],
        is_package_init: bool,
    ) -> str | None:
        """Resolve a relative import placeholder, updating AST nodes and import dicts.

        Returns the resolved module name, or None if the import was fully converted
        to submodule imports (and should be skipped by the caller).
        """
        assert self.resolver is not None
        rel_key = RelativeImportKey.decode(imported_name)
        level = rel_key.level
        partial = rel_key.partial or None

        resolved_name = self.resolver.resolve_relative(
            module_name, level, partial, is_package_init
        )
        if resolved_name is None:
            raise CompileError(
                "Relative import beyond top-level package",
                module_name, path, lineno=import_lineno
            )

        # Handle "from . import submod" case: each imported name might be a submodule
        all_converted_to_modules = False
        submod_imports: list[TpyImport] = []
        if partial is None and imported_name in ast.imports:
            import_items = ast.imports[imported_name]
            if isinstance(import_items, set):
                for orig_name, local_name in list(import_items):
                    submod_name = f"{resolved_name}.{orig_name}" if resolved_name else orig_name
                    submod_resolved = self.resolver.resolve(submod_name)
                    if submod_resolved:
                        import_items.discard((orig_name, local_name))
                        ast.imports[submod_name] = set()
                        ast.bare_module_imports.add(submod_name)
                        ast.user_module_imports[submod_name] = import_lineno
                        ast.module_aliases[submod_name] = local_name
                        submod_imports.append(TpyImport(
                            module_name=submod_name,
                            loc=SourceLocation(import_lineno, 0)
                        ))
                        import_queue.append((submod_name, import_lineno))
                    elif not resolved_name:
                        raise CompileError(
                            f"Cannot import '{orig_name}' from root level",
                            module_name, path, lineno=import_lineno
                        )
                all_converted_to_modules = len(import_items) == 0

        if all_converted_to_modules:
            for i, stmt in enumerate(ast.top_level_stmts):
                if isinstance(stmt, TpyImport) and stmt.module_name == imported_name:
                    ast.top_level_stmts[i:i+1] = submod_imports
                    break
            ast.imports.pop(imported_name, None)
            ast.user_module_imports.pop(imported_name, None)
            return None
        elif submod_imports:
            for i, stmt in enumerate(ast.top_level_stmts):
                if isinstance(stmt, TpyImport) and stmt.module_name == imported_name:
                    ast.top_level_stmts[i+1:i+1] = submod_imports
                    break

        # Update TpyImport node with resolved name
        for stmt in ast.top_level_stmts:
            if isinstance(stmt, TpyImport) and stmt.module_name == imported_name:
                stmt.module_name = resolved_name
                break

        # Update imports dict key
        if imported_name in ast.imports:
            ast.imports[resolved_name] = ast.imports.pop(imported_name)
        ast.user_module_imports[resolved_name] = ast.user_module_imports.pop(imported_name)
        if imported_name in ast.star_imports:
            ast.star_imports.discard(imported_name)
            ast.star_imports.add(resolved_name)

        return resolved_name

    def _process_user_import(
        self,
        imported_name: str,
        import_lineno: int | None,
        module_name: str,
        path: Path,
        builtin_names: set[str],
        new_chain: list[str],
        parent_ast: TpyModule | None = None,
    ) -> None:
        """Resolve a user module import, checking for not-found and builtin shadowing."""
        assert self.resolver is not None
        resolved = self.resolver.resolve(imported_name)
        if resolved is None:
            if imported_name in builtin_names:
                return
            raise CompileError(
                f"Module '{imported_name}' not found",
                module_name, path, lineno=import_lineno
            )
        if imported_name in builtin_names:
            if imported_name not in self.shadowed_builtins:
                self.shadowed_builtins[imported_name] = set()
            self.shadowed_builtins[imported_name].add((module_name, import_lineno))
        # Note: TpyImport injection for parser-keyword modules was removed.
        # All modules now emit TpyImport at parse time (except _IMPLICIT_MODULES).
        self._discover_package_inits(imported_name, new_chain, import_lineno)
        self._discover_modules(resolved.canonical_name, resolved.path, new_chain, import_lineno,
                               is_package_init=resolved.is_package_init)
        # `from pkg import submod`: each imported name might be a submodule
        # (as opposed to a value in pkg). Mirrors the relative-import handler:
        # when the resolver finds `pkg.submod`, also pull it into the build
        # set so consumer code can do `submod.X(...)` qualified access.
        if parent_ast is not None and resolved.canonical_name in parent_ast.imports:
            self._discover_absolute_submodule_imports(
                parent_ast, resolved.canonical_name, new_chain, import_lineno,
            )

    def _discover_absolute_submodule_imports(
        self, ast: TpyModule, package_name: str, import_chain: list[str],
        import_lineno: int | None,
    ) -> None:
        """For `from pkg import X` where X resolves to a submodule, pull
        `pkg.X` into the build set and bind it as a module alias on the
        importing AST so consumer code can do `X.SomeType` qualified access
        and `X.fn(...)` qualified calls.

        Mirrors the relative-import path's submodule conversion (in
        ``_resolve_relative_import``) but for absolute imports. Names that
        don't resolve as submodules are left alone -- they're presumably
        values / types in the package's exports.
        """
        assert self.resolver is not None
        names = ast.imports.get(package_name)
        if not isinstance(names, set):
            return
        promoted = False
        for orig_name, local_name in list(names):
            submod_name = f"{package_name}.{orig_name}"
            submod_resolved = (
                self.resolver.resolve(submod_name)
                if submod_name not in self.modules else None
            )
            if submod_name not in self.modules and submod_resolved is None:
                continue
            # Promote `from pkg import submod` to a full submodule import so
            # downstream lookups (qualified types, qualified calls) resolve
            # against pkg.submod's exports rather than treating submod as a
            # name in pkg.
            names.discard((orig_name, local_name))
            ast.imports.setdefault(submod_name, set())
            ast.bare_module_imports.add(submod_name)
            ast.user_module_imports.setdefault(submod_name, import_lineno or 0)
            if local_name != submod_name:
                ast.module_aliases[submod_name] = local_name
            promoted = True
            if submod_resolved is not None:
                self._discover_package_inits(submod_name, import_chain, import_lineno)
                self._discover_modules(
                    submod_resolved.canonical_name, submod_resolved.path,
                    import_chain, import_lineno,
                    is_package_init=submod_resolved.is_package_init,
                )
        # The parser's reverse-alias cache is built once at parse time;
        # ask the resolver to refresh it now so qualified-name lookup sees
        # the freshly promoted module aliases.
        if promoted and ast.resolver is not None:
            ast.resolver.refresh_module_aliases()

    def _discover_imports(self, module_name: str, ast: TpyModule,
                          import_chain: list[str]) -> None:
        """Discover imported modules from a parsed AST.

        Used for source-input compilation (from_source) to discover library
        dependencies without requiring a file on disk.
        """
        builtin_names = get_builtin_module_names()
        import_queue = list(ast.user_module_imports.items())
        processed = set()
        while import_queue:
            imported_name, import_lineno = import_queue.pop(0)
            if imported_name in processed:
                continue
            processed.add(imported_name)

            if RelativeImportKey.is_placeholder(imported_name):
                # Relative imports not supported for source input
                continue

            self._process_user_import(
                imported_name, import_lineno, module_name,
                Path("<stdin>"), builtin_names, import_chain, parent_ast=ast
            )

    def _discover_package_inits(self, dotted_name: str, import_chain: list[str],
                                 import_lineno: int | None) -> None:
        """Ensure all parent package __init__.py files are discovered.

        For "a.b.c", ensures a/__init__.py and a/b/__init__.py are discovered
        (if they exist) before a/b/c.py.

        Args:
            dotted_name: Dotted module path (e.g., "mypackage.submod").
            import_chain: Current import chain for cycle detection.
            import_lineno: Line number of the import.
        """
        assert self.resolver is not None
        parts = dotted_name.split('.')
        for i in range(1, len(parts)):
            package = '.'.join(parts[:i])
            if package not in self.modules:
                resolved = self.resolver.resolve(package)
                if resolved and resolved.is_package_init:
                    self._discover_modules(package, resolved.path, import_chain, import_lineno,
                                           is_package_init=True)

    def _compute_compile_order(self) -> None:
        """Compute compilation order via Tarjan SCC + condensation topo sort.

        Cyclic imports are accepted at the discovery layer. Tarjan
        collapses the import graph into SCCs; the condensation is
        acyclic and gets a deterministic topo sort. Within each SCC,
        members are ordered alphabetically for stability. Non-cyclic
        codebases yield identical output to the prior Kahn's algorithm.

        Each non-trivial SCC (>= 2 modules, or a self-loop) goes through
        `_reject_unsupported_cycle` which enforces the v1 conservatism
        gate (top-level statements not in allowlist, by-value cross-
        module type-completeness cycles, re-export facades inside cycles).
        """
        # Build adjacency: module -> list of in-workspace deps
        succ: dict[str, list[str]] = {name: [] for name in self.modules}
        for name, compiled in self.modules.items():
            for dep_name in compiled.ast.user_module_imports:
                if dep_name in self.modules:
                    succ[name].append(dep_name)

        # Tarjan SCC. Iterative for safety on deep import graphs.
        index_counter = [0]
        stack: list[str] = []
        on_stack: set[str] = set()
        index: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        sccs: list[list[str]] = []

        def _strongconnect(start: str) -> None:
            # Iterative DFS via an explicit work stack of (node, iterator).
            work: list[tuple[str, list[str], int]] = []
            index[start] = index_counter[0]
            lowlink[start] = index_counter[0]
            index_counter[0] += 1
            stack.append(start)
            on_stack.add(start)
            work.append((start, sorted(succ[start]), 0))
            while work:
                v, neighbors, i = work[-1]
                if i < len(neighbors):
                    w = neighbors[i]
                    work[-1] = (v, neighbors, i + 1)
                    if w not in index:
                        index[w] = index_counter[0]
                        lowlink[w] = index_counter[0]
                        index_counter[0] += 1
                        stack.append(w)
                        on_stack.add(w)
                        work.append((w, sorted(succ[w]), 0))
                    elif w in on_stack:
                        lowlink[v] = min(lowlink[v], index[w])
                else:
                    work.pop()
                    if work:
                        parent = work[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[v])
                    if lowlink[v] == index[v]:
                        component: list[str] = []
                        while True:
                            w = stack.pop()
                            on_stack.discard(w)
                            component.append(w)
                            if w == v:
                                break
                        sccs.append(sorted(component))

        for name in sorted(self.modules):
            if name not in index:
                _strongconnect(name)

        # Tarjan emits SCCs in reverse topo order (callees before callers
        # in successor-edge sense, i.e. deps before dependents).
        # Reject unsupported cycle shapes before the condensation topo
        # sort uses them.
        for component in sccs:
            if len(component) > 1 or component[0] in succ.get(component[0], []):
                self._reject_unsupported_cycle(component)

        # Record cycle peers so codegen can emit `<peer>_fwd.hpp`
        # includes for SCC members and avoid the cyclic complete-
        # header include problem.
        self._cycle_peers = {}
        for component in sccs:
            if len(component) > 1 or component[0] in succ.get(component[0], []):
                peers = frozenset(component)
                for member in component:
                    self._cycle_peers[member] = peers

        result: list[str] = []
        for component in sccs:
            result.extend(component)

        # Move implicit stdlib modules (and their submodules) to the front
        # so they're analyzed before any user code. The topological sort
        # already respects inter-module dependencies (e.g. typing depending
        # on tpy._typing), so we preserve that order -- just partition
        # implicit modules to the front.
        implicit_set = self._implicit_stdlib_set()
        if implicit_set:
            implicit = [m for m in result if m in implicit_set]
            result = implicit + [m for m in result if m not in implicit_set]
        self.compile_order = result

    def _check_workspace_completeness_cycles(self) -> None:
        """Reject by-value cross-module cycles with a structured diagnostic.

        Walks every cycle member's records (parents, fields) and rejects
        positions that require a peer cycle member's *complete* type --
        positions where the cycle's `<peer>_fwd.hpp` is insufficient
        and the C++ build would otherwise fail with a complete-type error
        ("invalid use of incomplete type", "field has incomplete type",
        ...). The check runs after decl finalization so RecordInfo
        fields / parents are populated.

        v1 conservative gate. Detected positions:
        - **Concrete inheritance**: `class A(Peer):` always needs `Peer`
          complete.
        - **By-value record field**: `field: peer_record_type` (the
          field stores the record by value -- only matters for
          `ValueType` records but we flag any by-value position to
          give a useful error before C++ does).
        - **`Own[T]` field**: `field: Own[peer_record]` -- ownership
          transfer, value layout.
        - **Tuple field**: `field: tuple[peer_record, ...]`.
        - **Value-variant union field**: `field: peer_record | other`
          where the variant is value-kind.
        - **Container of by-value cycle peer**: `field: list[peer]`,
          `set[peer]`, `Array[peer, N]`, etc. -- containers store
          elements by value internally.

        Forward-declarable positions (NOT flagged):
        - `Ptr[peer]`, `peer&` (via RefType wrapping).
        - Pointer-variant unions (the typical non-value record union).
        - Optional with pointer rep (`OptionalType.uses_pointer_repr`).
        - `@dynamic` protocol references (base-class fwd suffices).

        Diagnostic shape names the offending field path, the cycle
        members in the SCC, and points the user at the workaround
        (use `Ptr[T]` / `Optional[T]` instead of by-value).
        """
        if not self.compile_order:
            return
        # Use the cycle-peers map produced by `_compute_compile_order`
        # via Tarjan SCC -- only modules already in a non-trivial
        # import SCC need checking. Records in non-cyclic modules can
        # always be made complete via a normal #include.
        if not self._cycle_peers:
            return
        # Group cycle members by SCC for the diagnostic.
        seen_components: set[frozenset[str]] = set()
        for peers in self._cycle_peers.values():
            if peers in seen_components:
                continue
            seen_components.add(peers)
            self._reject_completeness_cycle_in_scc(peers)

    def _reject_completeness_cycle_in_scc(self, scc: frozenset[str]) -> None:
        """Inspect every record in this SCC for complete-type cross-cycle
        references; raise if any. The diagnostic names the first
        offender (deterministic order over modules + record name +
        field name)."""

        def _find_complete_required_peer(
            typ, current_module: str,
            _seen: set[int] | None = None,
        ) -> tuple[str, str] | None:
            """Return (peer_module, type_name) for the first cycle-peer
            record this type stores by value, or None. `_seen` is a
            per-traversal cycle guard against recursive type aliases
            that would otherwise loop on the NominalType / type_args
            descent. It must NOT be shared across separate field
            checks -- a NominalType singleton referenced by two
            distinct fields would silently skip the second on a
            shared set, hiding a real completeness violation.
            """
            if _seen is None:
                _seen = set()
            tid = id(typ)
            if tid in _seen:
                return None
            _seen.add(tid)
            # Strip wrappers that don't change layout.
            if isinstance(typ, ReadonlyType):
                return _find_complete_required_peer(typ.wrapped, current_module, _seen)
            # Pointer / reference: forward-declarable.
            if isinstance(typ, (PtrType, RefType)):
                return None
            # Own[T]: ownership transfer, value layout -> complete required.
            if isinstance(typ, OwnType):
                return _find_complete_required_peer(typ.wrapped, current_module, _seen)
            # Optional with pointer repr: T*-stored, forward-declarable.
            if isinstance(typ, OptionalType):
                if typ.uses_pointer_repr():
                    return None
                return _find_complete_required_peer(typ.inner, current_module, _seen)
            # Union: pointer-variant uses pointers (forward-declarable);
            # value-variant uses std::variant (complete required).
            if isinstance(typ, UnionType):
                if not all(m.is_value_type() for m in typ.members):
                    return None
                for m in typ.members:
                    found = _find_complete_required_peer(m, current_module, _seen)
                    if found is not None:
                        return found
                return None
            # Tuple: by-value, complete required for all elements.
            if isinstance(typ, TupleType):
                for m in typ.element_types:
                    found = _find_complete_required_peer(m, current_module, _seen)
                    if found is not None:
                        return found
                return None
            # NominalType: user record, builtin container, or protocol.
            if isinstance(typ, NominalType):
                if typ.is_user_record:
                    qname = typ._module_qname
                    if qname:
                        module = qname.rsplit('.', 1)[0]
                        if module != current_module and module in scc:
                            return (module, typ.name)
                    # Generic-record instantiation: a non-cycle-peer
                    # record parameterized over a cycle-peer arg
                    # (e.g. `Box[B]`) still needs the peer's complete
                    # layout when the template instantiates. Descend
                    # into type_args so we don't miss those.
                    if typ.type_args:
                        for arg in typ.type_args:
                            if not hasattr(arg, "is_value_type"):
                                continue
                            found = _find_complete_required_peer(arg, current_module, _seen)
                            if found is not None:
                                return found
                    return None
                if typ.is_protocol and not typ.is_dynamic_protocol:
                    # Static protocol used as a type: template constraint
                    # needs complete concept.
                    qname = typ._module_qname
                    if qname:
                        module = qname.rsplit('.', 1)[0]
                        if module != current_module and module in scc:
                            return (module, typ.name)
                    return None
                # Builtin container (list / dict / set / Array / ...):
                # walk args looking for cycle-peer references.
                if typ.type_args:
                    for arg in typ.type_args:
                        if not hasattr(arg, "is_value_type"):
                            continue  # int literal, string literal, etc.
                        found = _find_complete_required_peer(arg, current_module, _seen)
                        if found is not None:
                            return found
            return None

        for member in sorted(scc):
            compiled = self.modules.get(member)
            if compiled is None or compiled.analyzer is None:
                continue
            registry = compiled.analyzer.registry
            for record_name, record_info in registry.records.items():
                if record_info.defining_module != member:
                    continue  # imported records flow through their owning module's check
                # Concrete inheritance from a peer in the same SCC.
                for parent in record_info.parents:
                    if isinstance(parent, NominalType) and parent.is_user_record:
                        qn = parent._module_qname
                        if not qn:
                            continue
                        parent_mod = qn.rsplit('.', 1)[0]
                        if parent_mod != member and parent_mod in scc:
                            cycle_repr = " <-> ".join(sorted(scc))
                            # RecordInfo carries no source location; pin
                            # to the first cross-cycle import in this
                            # module so the diagnostic has a usable line.
                            lineno_n = self._first_cycle_import_line(compiled, scc)
                            raise CompileError(
                                f"Cyclic import: '{record_name}' in "
                                f"'{member}' concretely inherits from "
                                f"'{parent.name}' (defined in "
                                f"'{parent_mod}'). Concrete inheritance "
                                f"is not allowed across an import "
                                f"cycle; inherit from a `@dynamic` "
                                f"protocol or break the cycle. Cycle: "
                                f"{cycle_repr}",
                                member, compiled.path, lineno=lineno_n,
                            )
                # By-value field referencing a peer record.
                for field in record_info.fields:
                    found = _find_complete_required_peer(field.type, member)
                    if found is not None:
                        peer_mod, peer_name = found
                        cycle_repr = " <-> ".join(sorted(scc))
                        lineno_n = field.loc.line if field.loc else None
                        raise CompileError(
                            f"Cyclic import: field "
                            f"'{record_name}.{field.name}' stores "
                            f"'{peer_name}' from '{peer_mod}' by value. "
                            f"Cycle members can only reference each "
                            f"other through `Ptr[T]`, references, "
                            f"`Optional[T]` (when `T` is a non-value "
                            f"type), or pointer-variant union "
                            f"positions. Use `Ptr[{peer_name}]` or "
                            f"move the field out of the cycle. Cycle: "
                            f"{cycle_repr}",
                            member, compiled.path, lineno=lineno_n,
                        )

            # In-header-bodied functions: codegen emits the function
            # *definition* in the .hpp when any of these holds:
            # generic (type_params), @cpp_template stub, any Fn-typed
            # param, or any static (non-@dynamic) protocol param. For
            # cycle members, the .hpp only includes peer fwd headers,
            # so any by-value cross-cycle peer in those signatures
            # fails at C++ instantiation time. Reject at sema with a
            # structured diagnostic. Mirrors
            # `FunctionGenerator.is_template_function` so the gate's
            # set of "templated" functions matches what codegen
            # actually emits inline.
            for func in compiled.ast.functions:
                if func.is_overload_stub:
                    continue
                if not _is_template_emitted_in_header(func):
                    continue
                self._check_signature_for_cross_cycle_by_value(
                    func, member, scc, compiled,
                    name_label=f"'{func.name}'",
                    where_label="function",
                    finder=_find_complete_required_peer,
                )

            # Methods on cycle-member records: a non-generic record
            # emits non-templated methods to .cpp, but generic records
            # emit *all* methods inline in the header, and any
            # individual method that is itself templated (type_params,
            # Fn-typed param, static protocol param) emits its body
            # inline regardless of the record. Walk both.
            ast_records_by_name = {
                r.name: r for r in compiled.ast.all_records()
            }
            for record_name, record_info in registry.records.items():
                if record_info.defining_module != member:
                    continue
                record_is_generic = bool(record_info.type_params)
                ast_record = ast_records_by_name.get(record_name)
                if ast_record is None:
                    continue
                # Method overload groups -- `analyzer.overload_groups`
                # is populated for methods only during body sema (in
                # `_analyze_record_methods`), which has not run yet
                # when this gate fires. Build the index locally from
                # the AST instead. Codegen
                # (`records.py:_method_can_be_out_of_line`) keeps any
                # overload-dispatched impl inline-in-struct because
                # each specialization is emitted under a different
                # mangled name in the struct body, so the .hpp needs
                # the peer's complete layout.
                overload_dispatched_names = {
                    m.name for m in ast_record.methods if m.is_overload_stub
                }
                for method in ast_record.methods:
                    if method.is_overload_stub:
                        continue
                    method_inline = (
                        record_is_generic
                        or _is_template_emitted_in_header(method)
                        # __init__/__del__ are always emitted inline
                        # in the struct (codegen excludes them from
                        # out-of-line emission), so their body needs
                        # the peer's complete layout in the .hpp.
                        or method.name in ("__init__", "__del__")
                        or method.name in overload_dispatched_names
                    )
                    if not method_inline:
                        continue
                    self._check_signature_for_cross_cycle_by_value(
                        method, member, scc, compiled,
                        name_label=f"'{record_name}.{method.name}'",
                        where_label="method",
                        finder=_find_complete_required_peer,
                    )

            # Recursive type aliases that reference cycle peers by-value
            # in their body. TPy synthesizes a wrapper struct for these,
            # so the body needs the peer's complete layout -- same
            # constraint as a record field.
            recursive_aliases = (
                compiled.ast.recursive_union_names
                if compiled.ast.recursive_union_names else set()
            )
            for alias_name, (alias_type, alias_loc) in compiled.ast.type_aliases.items():
                if alias_name not in recursive_aliases:
                    continue
                found = _find_complete_required_peer(unwrap_ref_type(alias_type), member)
                if found is None:
                    continue
                peer_mod, peer_name = found
                cycle_repr = " <-> ".join(sorted(scc))
                lineno_n = alias_loc.line if alias_loc else None
                raise CompileError(
                    f"Cyclic import: recursive type alias "
                    f"'{alias_name}' in '{member}' references "
                    f"'{peer_name}' from '{peer_mod}' by value. "
                    f"Recursive aliases store their members directly, "
                    f"which is not allowed across an import cycle. "
                    f"Use `Ptr[{peer_name}]` or move the alias out of "
                    f"the cycle. Cycle: {cycle_repr}",
                    member, compiled.path, lineno=lineno_n,
                )

    def _check_signature_for_cross_cycle_by_value(
        self, func, member: str, scc, compiled, *,
        name_label: str, where_label: str, finder,
    ) -> None:
        """Walk a function/method signature for by-value cross-cycle
        peer references and raise a structured diagnostic if found.

        `finder` is the SCC-bound `_find_complete_required_peer`
        closure from the caller; it descends through containers /
        unions / tuples / generic records.
        """
        positions: list[tuple[str, TpyType]] = [("return type", func.return_type)]
        for pname, ptype in func.params:
            positions.append((f"parameter '{pname}'", ptype))
        for pos_label, pos_type in positions:
            found = finder(unwrap_ref_type(pos_type), member)
            if found is None:
                continue
            peer_mod, peer_name = found
            cycle_repr = " <-> ".join(sorted(scc))
            lineno_n = func.loc.line if func.loc else None
            raise CompileError(
                f"Cyclic import: {pos_label} of {name_label} in "
                f"'{member}' is '{peer_name}' from '{peer_mod}' by "
                f"value. Generic, `Fn`-typed, static-protocol-typed, "
                f"and `@cpp_template` {where_label}s (and methods on "
                f"generic records, `__init__`/`__del__`, and "
                f"overload-dispatched methods) are monomorphized at "
                f"every call site, so their parameter and return "
                f"types must be fully visible to callers -- which is "
                f"not allowed across an import cycle. Pass the peer "
                f"through a `Ptr[T]`, reference, or `@dynamic` "
                f"protocol position, or move the {where_label} out "
                f"of the cycle. Cycle: {cycle_repr}",
                member, compiled.path, lineno=lineno_n,
            )

    @staticmethod
    def _first_cycle_import_line(
        compiled: CompiledModule, cycle_members,
    ) -> int | None:
        """Source line of the first import in `compiled` that targets
        a cycle peer, used to pin completeness / facade reject
        diagnostics. None when the module has no such import (rare;
        falls back to file-level error)."""
        cycle_set = set(cycle_members)
        for stmt in compiled.ast.top_level_stmts or []:
            if isinstance(stmt, TpyImport) and stmt.module_name in cycle_set:
                return stmt.loc.line if stmt.loc else None
        return None

    def _reject_unsupported_cycle(self, component: list[str]) -> None:
        """Conservative reject gate for cycle members.

        Three rejection categories:

        1. **Top-level statements not in the allowlist.** Cycle
           members must consist of declarations and imports only --
           any executable top-level code (calls, conditionals, loops,
           assignments other than empty `name: T` decls) breaks the
           order-independent body-sema invariant.
        2. **C++ type-completeness cycles.** Tarjan SCC over the
           *resolved type-dependency graph* (by-value fields,
           value-types in containers / tuples / unions / Own /
           value-variants, concrete inheritance, value enum members,
           by-value cross-module function param/return where the
           function definition needs full layout). Any SCC there is
           rejected -- forward declarations alone do not provide the
           layout the C++ back-end needs.
        3. **Re-export facades** in cycles (`is_package_init`,
           `# tpy: native_module`, implicit stdlib facade members).
           Their re-export semantics conflict with the simple decl-
           registration ordering used inside an SCC.

        v1 implementation: only category 1 + category 3 are checked
        here. Category 2 is enforced by the C++ build the user runs
        (a cycle through by-value fields produces a complete-type
        compile error). A future tightening can move category-2 into
        sema with a dedicated diagnostic; the design pre-decides the
        full position list.
        """
        # Self-loop ('a' imports 'a'): degenerate; the parser should
        # already prevent this, but treat defensively.
        members = sorted(component)
        # Category 3 (re-export facades cannot be cycle members) was
        # dropped in Phase 5 of the per-module attribute table refactor.
        # Universal re-export plus cycle-aware `using` suppression in
        # codegen lets package __init__ / native_module / implicit
        # stdlib facades sit inside cycles without breaking the C++
        # build. The order-independent body sema invariant is
        # protected by Category 1 below (no executable top-level code).
        # Category 1: top-level statements not in the allowlist.
        # Allowed: TpyImport, TpyVarDecl with init=None whose codegen is
        # inert (declarations only). The synthetic `__name__` Final[str]
        # is injected later (sub-phase 3); at this discovery-time check
        # we only see user-authored statements.
        for name in members:
            compiled = self.modules.get(name)
            if compiled is None:
                continue
            for stmt in compiled.ast.top_level_stmts or []:
                if isinstance(stmt, TpyImport):
                    continue
                if isinstance(stmt, TpyVarDecl) and stmt.init is None:
                    continue
                cycle_repr = " <-> ".join(members)
                lineno_n = stmt.loc.line if stmt.loc else None
                raise CompileError(
                    f"Cyclic import members may only contain imports "
                    f"and bare type declarations at module level; "
                    f"found executable code in '{name}'. Move the "
                    f"statement into a function, or break the cycle. "
                    f"Cycle: {cycle_repr}",
                    name, compiled.path, lineno=lineno_n,
                )

    def _pre_populate_decl_exports(self, compiled: CompiledModule) -> None:
        """Mint skeleton RecordInfo / FunctionInfo / ProtocolInfo / enum
        NominalType objects from the parsed AST and stash them in
        `compiled.exports`. Runs once per module after type resolution
        and before any module's `_finalize_declarations`.

        The skeletons exist so peers' `bind_imports` can find a stable
        Python object for each cross-module imported name -- including
        between cycle members where neither module's full sema has
        completed when the other binds. Sub-phase 2/3 of
        `_finalize_declarations` sees the skeleton via
        `ctx.module_decl_exports` (set in `_finalize_declarations`) and
        mutates its fields in place rather than allocating a new object,
        so peer registries that captured a reference at bind-imports
        time stay live.
        """
        ast = compiled.ast
        if ast is None:
            return
        module_name = compiled.name
        public = public_module_name(module_name, ast.directives.cpp_namespace) or module_name
        # Records: skeletons carry just identity + type-params so cross-
        # module type references can find them; fields/methods/parents
        # fill in during sub-phase 2 via in-place mutation in
        # register_record (which adopts the skeleton via _adopt_skeleton).
        # Skip @builtin_type records (Int32, Float32, list, dict, ...) --
        # those already have authoritative TypeDef entries; pre-mining a
        # skeleton would pollute `_user_qname_index` via the implicit
        # stdlib registration loop and shadow the real RecordInfo at
        # conformance-check time.
        for record in ast.all_records():
            if record.builtin_type_key:
                continue
            if record.name in compiled.exports.records:
                continue
            skel = RecordInfo(
                name=record.name,
                fields=[],
                module=public,
                defining_module=module_name,
                type_params=list(record.type_params) if record.type_params else [],
                type_param_kinds=(list(record.type_param_kinds)
                                  if record.type_param_kinds else []),
            )
            compiled.exports.records[record.name] = skel
            install_binding(
                compiled.module_attributes, record.name,
                SymbolKind.RECORD, skel,
            )
        # Protocols: skeleton carries identity. `register_protocol`
        # adopts it via `_adopt_skeleton` so peers' references see the
        # freshly-finalized methods / parents.
        # Also pre-attach the skeleton onto the TypeDef registry under
        # the canonical qname so the parser-level type resolver
        # (`type_resolver.py`'s `_resolve_registered_type`) can detect
        # this name as a protocol BEFORE its defining module's
        # register_protocol runs. Without this, cycle members that
        # import a peer protocol would see `is_protocol=False` on the
        # resolved NominalType (TypeDef.protocol still None at resolve
        # time) and method dispatch would fail.
        for protocol in ast.protocols:
            if protocol.name in compiled.exports.protocols:
                continue
            skel = ProtocolInfo(
                name=protocol.name,
                methods=[],
                type_params=list(protocol.type_params) if protocol.type_params else [],
                module=public,
                is_dynamic=protocol.is_dynamic,
            )
            compiled.exports.protocols[protocol.name] = skel
            attach_dynamic_type_def(
                f"{public}.{protocol.name}",
                TypeCategory.PROTOCOL,
                protocol=skel,
            )
            install_binding(
                compiled.module_attributes, protocol.name,
                protocol_kind_for(protocol.is_dynamic), skel,
            )
        # Enums: pre-mint a NominalType (enum-kind) so peers can find
        # the enum by name during bind_imports. The qname matches what
        # `register_enum` will emit so the TypeDef-registry lookup
        # (`type_def_of(typ)`) finds the same `EnumInfo` regardless of
        # which analyzer's NominalType reference is used. The
        # NominalType itself is essentially an immutable identity --
        # peer references stay valid; the EnumInfo payload that
        # register_enum attaches to TypeDef is the per-qname source of
        # truth for member lists, underlying type, etc.
        for enum in ast.all_enums():
            if enum.name in compiled.exports.enums:
                continue
            enum_nominal = NominalType(
                enum.name, type_args=(),
                _module_qname=f"{public}.{enum.name}",
            )
            compiled.exports.enums[enum.name] = enum_nominal
            install_binding(
                compiled.module_attributes, enum.name,
                SymbolKind.ENUM, enum_nominal,
            )
        # Functions: skeletons carry just `name` + a placeholder return
        # type; sub-phase 3 mutates in place. The empty params list is
        # fine -- peers only check that a name exists in
        # `module_info.functions[name]` during bind_imports; the
        # subsequent overload resolution in body sema reads the
        # then-mutated fields.
        for func in ast.functions:
            if func.is_overload_stub:
                # Stubs are grouped with their implementation in
                # `register_function_group`; don't pre-populate
                # individually -- the group is created together.
                continue
            if func.builtin_decorator_key:
                # `@builtin_decorator` markers register a fresh
                # FunctionInfo by design (decorator-key lookup, not
                # call resolution). They have no cross-module call
                # sites that would capture a skeleton at bind_imports.
                continue
            if func.name in compiled.exports.functions:
                continue
            fi_skel = FunctionInfo(
                name=func.name,
                params=[],
                return_type=VOID,
                qualified_name=f"{module_name}.{func.name}",
                originating_module=module_name,
            )
            fi_list = [fi_skel]
            compiled.exports.functions[func.name] = fi_list
            install_binding(
                compiled.module_attributes, func.name,
                SymbolKind.FUNCTION, fi_list,
            )

        # Snapshot identity of every skeleton we just minted, so
        # `_verify_skeleton_adoption` (called after the module's
        # declaration sub-phases finish) can confirm each registration
        # entrypoint adopted its skeleton in place via
        # `_adopt_skeleton`. Catches future drift if a new entrypoint
        # builds a fresh sema object instead of mutating the skeleton --
        # peer registries that captured the skeleton at bind_imports
        # time would otherwise see stale data.
        #
        # Enums are intentionally excluded: enum semantics route
        # through the qname-keyed TypeDef registry rather than
        # NominalType identity, so the skeleton NominalType is just
        # a name carrier and `register_enum` is free to allocate a
        # fresh instance.
        compiled._skeleton_ids = _SkeletonSnapshot(
            records={n: id(o) for n, o
                     in compiled.exports.records.items()},
            protocols={n: id(o) for n, o
                       in compiled.exports.protocols.items()},
            # Record both the list and inner FI identity: register_overload_group
            # mutates the skeleton list in place (list identity preserved);
            # register_function builds a new list but adopts the inner FI
            # (inner FI identity preserved). Either is a valid adoption shape.
            functions={
                n: (id(lst), id(lst[0])) if lst else (0, 0)
                for n, lst in compiled.exports.functions.items()
            },
        )

    def _canonicalize_import_sources(self, compiled: CompiledModule) -> None:
        """Rewrite the module's parser import table to defining modules.

        Runs once per module right before `_analyze_module`.  By this
        point all dependencies (in topological order) have been sema-
        analyzed, so `self.modules[dep].exports.{records,enums,
        protocols}` are populated.  Re-export facades share the defining
        module's RecordInfo / ProtocolInfo / enum NominalType by
        reference, so a single lookup per imported name suffices --
        transitive chains are already flattened in the shared objects.
        Canonical tuples let `TypeResolver` mint `_module_qname` on its
        first pass.
        """
        resolver = compiled.ast.resolver
        if resolver is None:
            return
        resolver.canonicalize_import_table(self._lookup_defining_module)
        # Make submodule-imported source modules visible to the parser's
        # type resolver so qualified types like `submod.X` (where the user
        # wrote `from pkg import submod`) can resolve to the submodule's
        # records / enums / protocols / type aliases. Without this the
        # resolver only sees names directly imported by this module.
        self._populate_submodule_registry(compiled)

    def _populate_submodule_registry(self, compiled: CompiledModule) -> None:
        """Register submodule ModuleInfo + their records into the importer's
        parser.registry so the type resolver can look up `submod.X` qualified
        types and downstream codegen can render them with cross-module qnames.
        """
        resolver = compiled.ast.resolver
        if resolver is None:
            return
        parser_registry = resolver.registry
        for mod_name in compiled.ast.user_module_imports:
            if mod_name in parser_registry.modules:
                continue
            dep_compiled = self.modules.get(mod_name)
            if dep_compiled is None:
                continue
            module_info = self._exports_to_module_info(
                mod_name, dep_compiled.exports, dep_compiled)
            parser_registry.modules[mod_name] = module_info
            # Hoist visible records under their short name so the type
            # resolver finds `submod.X` without needing a side-effecting
            # registration during type resolution.
            for short, rinfo in module_info.records.items():
                if parser_registry.get_record(short) is None:
                    parser_registry.register_record(rinfo, short)

    def _lookup_defining_module(
        self, surface_module: str, name: str,
    ) -> tuple[str, str] | None:
        """Return the (defining_module, canonical_name) tuple for a
        re-exported record/enum/protocol, or None if nothing to rewrite.

        The surface module in parser's name_index is the *public*
        identity (`public_module_name` applied at import time, plus the
        `_PRIVATE_MODULE_PUBLIC_NAMES` overrides).  When this function
        runs on module M in topological order, M's private-submodule
        dependencies (e.g. `tpy._core._types`) are already analyzed
        but their shared public facade (`tpy`, `typing`) may not be.
        Fall back to scanning the private submodules that collapse to
        the surface -- their exports are populated and carry the same
        RecordInfo / ProtocolInfo / enum NominalType by reference, so
        the canonical tuple they yield is identical to what the facade
        would produce later.
        """
        result = self._lookup_in_module(surface_module, name)
        if result is not None:
            return result
        for candidate in self._private_submodules_for(surface_module):
            result = self._lookup_in_module(candidate, name)
            if result is not None:
                return result
        return None

    def _private_submodules_for(self, surface_module: str) -> list[str]:
        """Enumerate candidate raw private submodules that collapse to
        `surface_module` under `public_module_name` / the explicit
        `_PRIVATE_MODULE_PUBLIC_NAMES` overrides. Cached across
        `_canonicalize_import_sources` calls within a compilation."""
        if self._public_to_raw_cache is None:
            self._public_to_raw_cache = self._build_public_to_raw_cache()
        return self._public_to_raw_cache.get(surface_module, [])

    def _build_public_to_raw_cache(self) -> dict[str, list[str]]:
        cache: dict[str, list[str]] = {}
        for raw_name, compiled in self.modules.items():
            override = _PRIVATE_MODULE_PUBLIC_NAMES.get(raw_name)
            if override is not None:
                cache.setdefault(override, []).append(raw_name)
                continue
            if "._" not in raw_name:
                continue
            cpp_ns = compiled.ast.directives.cpp_namespace
            public = _public_module_name_of(raw_name, cpp_ns)
            if public and public != raw_name:
                cache.setdefault(public, []).append(raw_name)
        return cache

    def lookup_attribute(self, module_qname: str, name: str) -> 'BindingCell | None':
        """Compiler-level entry point for module-attribute lookups.

        Returns the `BindingCell` for `name` in module `module_qname`,
        or None if either the module is unknown or the name has no
        binding. Pre-population guarantees local definitions are
        visible after `_pre_populate_decl_exports` runs; cross-module
        imports / re-exports populate during the dep's
        `_finalize_declarations` (and via Phase 5 cycle pre-population
        for cycle peers).

        Reader entry point added in Phase 2 of the per-module attribute
        table refactor.
        """
        compiled = self.modules.get(module_qname)
        if compiled is None:
            return None
        return compiled.module_attributes.get(name)

    def _lookup_in_module(
        self, module_name: str, name: str,
        _seen: set[str] | None = None,
    ) -> tuple[str, str] | None:
        """Parser-level (defining_module, canonical_name) lookup for
        record / enum / protocol type names.

        Local definitions are read from `compiled.module_attributes`,
        populated by `_pre_populate_decl_exports` before any module's
        canonicalization runs. Re-export chains still follow
        `ast.imports` recursively because dep sema (which would
        materialize re-export bindings into the table) hasn't
        completed when canonicalize runs for the cycle's first edge.

        Type aliases / functions / globals are excluded by design --
        only canonical *type* names mint a `_module_qname` in
        `parser_imports.canonicalize_name_index`. Adding non-type
        names there would mark them `is_canonical` and short-circuit
        downstream resolution (e.g. Float64-the-alias would gain a
        spurious qname before its alias body is resolved).

        The `_seen` set guards against rare re-export-chain cycles.
        """
        if _seen is None:
            _seen = set()
        seen_key = f"{module_name}::{name}"
        if seen_key in _seen:
            return None
        _seen.add(seen_key)
        compiled = self.modules.get(module_name)
        if compiled is None or compiled.ast is None:
            return None
        ast = compiled.ast
        public_name = _public_module_name_of(
            module_name, ast.directives.cpp_namespace,
        ) or module_name
        # Local definitions take precedence over any same-named import.
        # Pre-populated bindings cover records, enums, and protocols
        # locally defined in `module_name`.
        cell = compiled.module_attributes.get(name)
        if cell is not None and cell.binding.defining_module is None:
            kind = cell.binding.kind
            if kind in (SymbolKind.RECORD, SymbolKind.ENUM,
                        SymbolKind.PROTOCOL_STATIC,
                        SymbolKind.PROTOCOL_DYNAMIC):
                return (public_name, name)
        # If `module_name` is a macro module that registers a class /
        # call / builder macro under `name`, treat the macro as locally
        # defined here. Lets the re-export chase canonicalize a chain
        # through plain modules to the ultimate macro module so
        # consumer parsers / sema look up the macro under the right key.
        if self._macro_registry is not None and (
            self._macro_registry.get_macro(module_name, name) is not None
            or self._macro_registry.get_call_macro(module_name, name) is not None
            or self._macro_registry.get_builder_macro(module_name, name) is not None
        ):
            return (module_name, name)
        # Re-export chase: scan `ast.imports` for an entry whose local
        # name matches `name`, then recurse into the source module.
        if ast.imports:
            for src_module, names in ast.imports.items():
                if not isinstance(names, set):
                    continue
                for original_name, local_name in names:
                    if local_name != name:
                        continue
                    nested = self._lookup_in_module(
                        src_module, original_name, _seen=_seen,
                    )
                    if nested is not None:
                        return nested
        return None

    def _resolve_module_refs(self, compiled: CompiledModule) -> None:
        """Run the parser-output ref walker on `compiled.ast`.

        Thin wrapper around `parse.resolve_refs.resolve_refs` so the
        compilation phase order is legible inline in `_analyze_module`.
        """
        from tpyc.parse.resolve_refs import resolve_refs
        resolve_refs(compiled.ast)

    def _resolve_types_for_module(self, compiled: CompiledModule) -> None:
        """Canonicalize imports and resolve TypeRefNodes for one
        module. The per-module pipeline reads as `resolve -> sema`;
        canonicalization is parser-AST-only so it does not require
        dep sema to have completed (cycle members can canonicalize
        each other before either has run sub-phases 2-3).
        """
        # Canonicalize the import table to defining modules so the
        # resolve phase that follows mints authoritative `_module_qname`
        # values for cross-module references on the first pass.
        self._canonicalize_import_sources(compiled)

        # Resolve every TypeRefNode the parser emitted at annotation
        # sites.  Runs after canonicalization (cross-module refs need
        # the canonical tuple) and before sema (sema reads TpyType
        # everywhere).  Macro-added method bodies are resolved later,
        # inside `register_record` post-macro-application, because the
        # methods don't exist yet at this point.
        self._resolve_module_refs(compiled)

    def _finalize_declarations(self, compiled: CompiledModule) -> SemanticAnalyzer:
        """Create the analyzer for `compiled`, register dep
        ModuleInfo, and run the three declaration-time sub-phases
        (`bind_imports`, `register_records_and_protocols`,
        `register_signatures`). Returns the analyzer so a subsequent
        body-sema pass can consume it.

        Compile loop runs all `_finalize_declarations` first, then
        all `_analyze_bodies` -- so by the time any module's body
        sema starts, every peer module's declarations are finalized
        and visible through `_shared_modules`.
        """
        # Create analyzer
        analyzer = SemanticAnalyzer(default_int_type=self.default_int_type,
                                    shared_modules=self._shared_modules)
        analyzer.ctx.macro_registry = self._macro_registry
        analyzer.ctx.allow_top_level_error_unwrap = self.allow_top_level_error_unwrap
        # Hand the pre-populated exports to the analyzer so the
        # registration paths can adopt skeleton objects from peers'
        # shared dict instead of allocating new ones (see
        # `_pre_populate_decl_exports`).
        analyzer.ctx.module_decl_exports = compiled.exports
        # Per-module attribute table (Phase 1). Aliased into the ctx so
        # registration paths and `_register_user_module_import` can
        # install bindings as they run; the canonical store lives on
        # the CompiledModule. See docs/MUTUAL_IMPORTS_DESIGN.md.
        analyzer.ctx.module_attributes = compiled.module_attributes

        # Register already-analyzed user modules in this analyzer's registry
        # This must happen before analyze() so _register_user_module_import can find them
        # Also register transitive dependencies so types referenced in method signatures
        # (e.g. iterator types) are visible even when not explicitly imported.
        # Skip implicit stdlib modules here -- they're handled separately below
        # with full protocol registration (including cpp_concept marker protocols).
        implicit_set = self._implicit_stdlib_set()
        registered: set[str] = set()
        queue = list(compiled.ast.user_module_imports)
        while queue:
            dep_name = queue.pop()
            if dep_name in registered or dep_name not in self.modules:
                continue
            registered.add(dep_name)
            if dep_name in implicit_set:
                continue
            dep_compiled = self.modules[dep_name]
            module_info = self._exports_to_module_info(dep_name, dep_compiled.exports, dep_compiled)
            analyzer.registry.register_module(module_info)
            # Register .py-defined protocols so resolve_type can find them
            # for qualified access (e.g. typing.Sized via bare `import typing`).
            for proto in module_info.protocols.values():
                if not proto.cpp_concept:
                    analyzer.registry.register_protocol(proto)
            self._index_builtin_type_records(module_info, analyzer)
            # Enqueue transitive dependencies
            for transitive in self.modules[dep_name].ast.user_module_imports:
                if transitive not in registered:
                    queue.append(transitive)

        # Register implicit stdlib modules (typing, tpy, and their submodules) --
        # their protocols are needed for builtin type-checking, and their
        # functions/types need to be available for import.
        # Always process these even if already seen in the user deps loop above,
        # because that loop filters out cpp_concept protocols (marker protocols
        # like Default, ValueType, etc.) which the implicit stdlib loop must register.
        for implicit_mod in implicit_set:
            if implicit_mod in self.modules:
                dep_compiled = self.modules[implicit_mod]
                module_info = self._exports_to_module_info(implicit_mod, dep_compiled.exports, dep_compiled)
                analyzer.registry.register_module(module_info)
                for proto in module_info.protocols.values():
                    analyzer.registry.register_protocol(proto)
                self._index_builtin_type_records(module_info, analyzer)
                for record in module_info.records.values():
                    if not record.builtin_type_key:
                        analyzer.registry.register_record(record)

        # Set module name for __name__
        module_name = "__main__" if compiled.is_entry_point else compiled.name

        # Run the three declaration-time sub-phases. Body sema (sub-phases 4
        # and 5) is deferred to `_analyze_bodies`.
        try:
            analyzer.bind_imports(compiled.ast, module_name=module_name)
            analyzer.register_records_and_protocols(compiled.ast)
            analyzer.register_signatures(compiled.ast)
        except SemanticError as e:
            if e.filename is None and not compiled.is_entry_point:
                e.filename = os.path.relpath(compiled.path)
            raise

        # Store analyzer for codegen and for the subsequent body-sema pass.
        compiled.analyzer = analyzer
        # Populate declaration exports right after sub-phases 1-3 so peer
        # modules' canonicalization can see authoritative records /
        # protocols / enums / functions / type_aliases / variables /
        # decl re-exports without waiting for our body sema. With
        # `register_globals` now in sub-phase 3, variable state is
        # already final at this point; both halves of `_extract_exports`
        # are safe to run here. The body-sema pass adds the `reached`
        # set and refreshes the workspace ModuleInfo entry.
        self._extract_declaration_exports(compiled, analyzer)
        self._extract_body_exports(compiled, analyzer)
        self._verify_skeleton_adoption(compiled)
        return analyzer

    @staticmethod
    def _verify_skeleton_adoption(compiled: CompiledModule) -> None:
        """Assert that every pre-populated skeleton in
        `compiled.exports` was adopted in place by its registration
        entrypoint. Bug-prevention check for the
        `_adopt_skeleton(skeleton, full)` invariant: peer registries
        that captured a skeleton reference during `bind_imports` will
        only see freshly-finalized data if the registration site
        mutated the skeleton in place rather than allocating a fresh
        sema object. Catches future drift when a new registration
        path is added.

        Identity rules per entry kind:

        * Records / protocols: object identity is preserved
          (`register_record` / `register_protocol` adopt the skeleton
          object directly).
        * Functions: `register_overload_group` mutates the skeleton
          *list* in place (list identity preserved), while
          `register_function` builds a fresh list but adopts the
          skeleton's inner `FunctionInfo` (inner FI identity
          preserved). Either shape is valid.

        Enums are excluded: enum semantics route through the
        qname-keyed TypeDef registry rather than NominalType
        identity, so the skeleton NominalType is just a name carrier
        and `register_enum` is free to allocate a fresh instance.
        """
        snap = compiled._skeleton_ids
        if snap is None:
            return
        mod = compiled.name
        # Records and protocols share the same identity rule: the
        # skeleton object is mutated in place by `_adopt_skeleton`,
        # so the export entry must remain the same object. Walk both
        # against `compiled.exports.records` / `.protocols`.
        for kind, skeleton_ids, exports_dict in (
            ("record", snap.records, compiled.exports.records),
            ("protocol", snap.protocols, compiled.exports.protocols),
        ):
            for name, skel_id in skeleton_ids.items():
                cur = exports_dict.get(name)
                if cur is None:
                    continue  # name was deleted (e.g. macro removed it)
                assert id(cur) == skel_id, (
                    f"skeleton-adoption invariant violated: {kind} "
                    f"'{name}' in module '{mod}' has a fresh "
                    f"identity. `register_{kind}` must adopt the "
                    f"pre-populated skeleton via `_adopt_skeleton`; "
                    f"otherwise peer registries that bound the "
                    f"skeleton at bind_imports time will see stale "
                    f"placeholder data."
                )
        # Functions accept either valid adoption shape: list mutated
        # in place (overload-group path) or inner FI preserved
        # (register_function path). Note this is a coarse check --
        # if a regression swapped the list for an unrelated list
        # whose [0] happens to be the original FI identity by
        # coincidence, this would still pass. Low-risk in practice.
        for name, (skel_list_id, skel_fi_id) in snap.functions.items():
            cur = compiled.exports.functions.get(name)
            # `(0, 0)` is the sentinel for an empty skeleton list,
            # which the snapshot type admits but pre-populate never
            # produces (every minted skeleton is `[FunctionInfo(...)]`).
            # Defensive skip; unreachable in practice today.
            if cur is None or skel_list_id == 0:
                continue
            if id(cur) == skel_list_id:
                continue
            if cur and id(cur[0]) == skel_fi_id:
                continue
            raise AssertionError(
                f"skeleton-adoption invariant violated: function "
                f"'{name}' in module '{mod}' has neither the "
                f"pre-populated list identity nor the pre-populated "
                f"inner FunctionInfo identity. Either `register_function` "
                f"failed to adopt the skeleton FI via `_adopt_skeleton`, "
                f"or `register_overload_group` failed to mutate the "
                f"skeleton list in place. Peer registries that bound "
                f"the skeleton at bind_imports time will see stale "
                f"placeholder params / VOID return."
            )
        # Snapshot served its purpose; clear so the dict doesn't
        # outlive the sema-phase window.
        compiled._skeleton_ids = None

    def _analyze_bodies(self, compiled: CompiledModule) -> None:
        """Run sub-phase 4 (analyze_bodies) and sub-phase 5
        (run_phase2_fixpoint) on the analyzer prepared by
        `_finalize_declarations`, then extract exports + compute
        reached symbols.
        """
        analyzer = compiled.analyzer
        assert analyzer is not None, (
            "_analyze_bodies called before _finalize_declarations populated "
            f"compiled.analyzer for module '{compiled.name}'"
        )
        module_name = "__main__" if compiled.is_entry_point else compiled.name
        try:
            analyzer.analyze_bodies(compiled.ast)
            analyzer.run_phase2_fixpoint(compiled.ast)
        except SemanticError as e:
            if e.filename is None and not compiled.is_entry_point:
                e.filename = os.path.relpath(compiled.path)
            raise

        # Emit warnings for shadowed builtins imported by this module.
        # Skip hybrid modules (builtin supplements a .py module).
        for shadowed_name, importers in self.shadowed_builtins.items():
            if shadowed_name in self.modules:
                continue
            for importing_module, lineno in importers:
                if importing_module == compiled.name or (compiled.is_entry_point and importing_module == ModuleResolver.get_module_name(self.entry_point)):
                    analyzer.ctx.diagnostics.append(Diagnostic(
                        DiagnosticLevel.WARNING,
                        f"import '{shadowed_name}' shadows builtin module",
                        SourceLocation(lineno or 0, 0)
                    ))

        # Body-level exports were already populated by
        # `_finalize_declarations` (since `register_globals` runs in
        # sub-phase 3). The remaining body-sema-dependent piece is the
        # `reached` set (computed below from the fully-analyzed AST).

        # Compute reached external symbols for include propagation. Stored
        # on analyzer.ctx for codegen and on compiled.exports so downstream
        # modules' _exports_to_module_info can carry it on ModuleInfo.
        reached = compute_reached_symbols(compiled.ast, analyzer, module_name)
        analyzer.ctx.reached = reached
        compiled.exports.reached = reached

        # Refresh this module's entry in `_shared_modules` with the now-
        # complete ModuleInfo (including body-level variables / final
        # markers / reached set). Peer analyzers in the same compilation
        # share the dict (see SemanticAnalyzer.__init__), so any module
        # whose `_analyze_bodies` runs after this one will see the
        # fully-populated entry. The declarations-pass setup loops only
        # ever construct ModuleInfo from a peer's *declaration* exports;
        # without this refresh, body sema would read a stale partial entry.
        analyzer.registry.register_module(
            self._exports_to_module_info(compiled.name, compiled.exports, compiled)
        )

    def _analyze_module(self, compiled: CompiledModule) -> None:
        """Analyze a single module: run declaration finalization then body
        sema. Thin wrapper preserving the legacy per-module analyze cycle;
        callers preferring the explicit two-stage form (Phase 6) call
        `_finalize_declarations` and `_analyze_bodies` directly.
        """
        self._finalize_declarations(compiled)
        self._analyze_bodies(compiled)

    def _extract_exports(self, compiled: CompiledModule, analyzer: SemanticAnalyzer) -> None:
        """Extract exports from a fully-analyzed module.

        Convenience wrapper that runs both halves in sequence; preserves
        the legacy single-call signature for callers that still drive the
        per-module analyze cycle directly.
        """
        self._extract_declaration_exports(compiled, analyzer)
        self._extract_body_exports(compiled, analyzer)

    def _extract_declaration_exports(
        self, compiled: CompiledModule, analyzer: SemanticAnalyzer,
    ) -> None:
        """Extract declaration-level exports (no body sema required).

        Populates `compiled.exports` with everything that's known after the
        three declaration sub-phases (`bind_imports`,
        `register_records_and_protocols`, `register_signatures`):
        functions, records, protocols, enums, type_aliases, and the
        recursive-union name set, plus their re-export entries (which
        depend only on the analyzer's import-binding tables and the
        registry indexes -- both populated by sub-phase 1+2).

        Idempotent: callers may invoke this between
        `_finalize_declarations` and `_analyze_bodies` so peer modules
        canonicalizing through `compiled.exports.{records,protocols,enums}`
        see authoritative entries even before bodies have been analyzed.
        Body-level export state lives in `_extract_body_exports`.
        """
        exports = compiled.exports
        # Universal re-export: every module exposes its imports as
        # module attributes, matching CPython semantics. Cycle peers
        # also re-export; codegen emits cycle-aware suppression for
        # `using` lines whose target sits in the same SCC and isn't
        # forward-declarable in `<peer>_fwd.hpp`.
        can_reexport = True

        # Export all user-defined functions
        exported_funcs: set[str] = set()
        for func in compiled.ast.functions:
            if func.is_overload_stub:
                continue
            func_infos = analyzer.registry.get_function(func.name)
            if func_infos:
                exports.functions[func.name] = func_infos
                exported_funcs.add(func.name)

        # Export @native overload groups (all stubs, no implementation)
        for func in compiled.ast.functions:
            if func.is_overload_stub and func.name not in exported_funcs:
                func_infos = analyzer.registry.get_function(func.name)
                if func_infos:
                    exports.functions[func.name] = func_infos
                    exported_funcs.add(func.name)

        # Re-export imported functions from user modules. Skip
        # special_handling stubs (native_global etc.) -- they're
        # compiler directives, not real functions. Track re-export
        # source as the ultimate defining module so consumer
        # `using` emissions land at the function's actual namespace,
        # not at an intermediate that may have suppressed its own
        # `using` line under cycle-aware codegen.
        if can_reexport:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_functions.items():
                if local_name in exports.functions:
                    continue
                module_info = analyzer.registry.get_module(source_module)
                func_infos = None
                if module_info and original_name in module_info.functions:
                    func_infos = module_info.functions[original_name]
                elif module_info and module_info.module_attributes:
                    # Cycle re-export fallback: the source's per-kind
                    # functions dict may still be empty (its
                    # _extract_declaration_exports hasn't run yet),
                    # but the attribute table was populated by
                    # `_pre_populate_reexport_bindings`.
                    cell = module_info.module_attributes.get(original_name)
                    if (cell is not None
                            and cell.binding.kind is SymbolKind.FUNCTION
                            and isinstance(cell.binding.info, list)):
                        func_infos = cell.binding.info
                if func_infos:
                    exports.functions[local_name] = func_infos
                    if not func_infos[0].special_handling:
                        ult_mod = func_infos[0].originating_module or source_module
                        ult_name = func_infos[0].name
                        exports.reexported_functions[local_name] = (ult_mod, ult_name)

        # Export all user-defined records (including nested)
        for record in compiled.ast.all_records():
            record_info = analyzer.registry.get_record(record.name)
            if record_info:
                exports.records[record.name] = record_info

        # Re-export imported records from user modules. Iterate the registry
        # and filter by imported_record_qualification (cross-module, non-builtin).
        # For aliased imports (`from X import P as MyP`) the record is registered
        # under both local and canonical names; skip the canonical-name duplicate
        # when an alias entry exists so re-export matches CPython `from` semantics.
        if can_reexport:
            aliased_record_ids = {
                id(rinfo) for ln, rinfo in analyzer.registry.records.items()
                if ln != rinfo.name
            }
            for local_name, record_info in analyzer.registry.records.items():
                if local_name == record_info.name and id(record_info) in aliased_record_ids:
                    continue
                qual = analyzer.registry.imported_record_qualification(
                    local_name, analyzer.ctx.module_name)
                if qual is None:
                    continue
                source_module, original_name = qual
                if local_name in exports.records:
                    continue
                module_info = analyzer.registry.get_module(source_module)
                if module_info and original_name in module_info.records:
                    exports.records[local_name] = module_info.records[original_name]
                    exports.reexported_records[local_name] = (source_module, original_name)

        # Export all user-defined protocols
        for protocol in compiled.ast.protocols:
            protocol_info = analyzer.registry.scan_by_short_name(protocol.name)
            if protocol_info:
                exports.protocols[protocol.name] = protocol_info

        # Re-export imported protocols from user modules. Falls back
        # to the source's `module_attributes` for the cycle-peer case
        # where the per-kind dict on `module_info.protocols` is still
        # empty when our extract runs (the source's
        # `_extract_declaration_exports` hasn't run yet).
        if can_reexport:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_protocols.items():
                if local_name in exports.protocols:
                    continue
                module_info = analyzer.registry.get_module(source_module)
                pinfo = None
                if module_info and original_name in module_info.protocols:
                    pinfo = module_info.protocols[original_name]
                elif module_info and module_info.module_attributes:
                    cell = module_info.module_attributes.get(original_name)
                    if (cell is not None and cell.binding.kind in (
                            SymbolKind.PROTOCOL_STATIC,
                            SymbolKind.PROTOCOL_DYNAMIC)):
                        pinfo = cell.binding.info
                if pinfo is not None:
                    exports.protocols[local_name] = pinfo

        # Export all user-defined enums (including nested)
        for enum in compiled.ast.all_enums():
            enum_type = analyzer.registry.get_enum(enum.name)
            if enum_type:
                exports.enums[enum.name] = enum_type

        # Re-export imported enums from user modules (same aliasing treatment
        # as records above).
        if can_reexport:
            aliased_enum_ids = {
                id(et) for ln, et in analyzer.registry.enums.items()
                if ln != et.name
            }
            for local_name, enum_type in analyzer.registry.enums.items():
                if local_name == enum_type.name and id(enum_type) in aliased_enum_ids:
                    continue
                qual = analyzer.registry.imported_enum_qualification(
                    local_name, analyzer.ctx.module_name)
                if qual is None:
                    continue
                source_module, original_name = qual
                if local_name in exports.enums:
                    continue
                module_info = analyzer.registry.get_module(source_module)
                if module_info and original_name in module_info.enums:
                    exports.enums[local_name] = module_info.enums[original_name]
                    exports.reexported_enums[local_name] = (source_module, original_name)

        # Export type aliases
        for name, (typ, _loc) in compiled.ast.type_aliases.items():
            exports.type_aliases[name] = typ
        exports.recursive_union_names = set(compiled.ast.recursive_union_names)

    def _extract_body_exports(
        self, compiled: CompiledModule, analyzer: SemanticAnalyzer,
    ) -> None:
        """Extract body-sema-dependent exports.

        After moving `register_globals` to sub-phase 3 (so cross-module
        `from X import VAR` resolves at decl-time), variable / final-
        marker / reexport state is actually known by the end of
        `_finalize_declarations`. This method now extracts them too --
        keeping the symmetric "decl + body" split intact while the
        actual data dependencies sit on the decl side. The body-sema
        pass would otherwise have nothing left to extract.
        """
        exports = compiled.exports
        # Mirrors `_extract_declaration_exports`: universal re-export.
        can_reexport = True

        # Export global variables (from top-level statements)
        # These are tracked in the global scope, but we must exclude imported variables
        # UNLESS they were redefined at the top level (in top_level_decls)
        # Exception: package __init__.py files and stdlib facade modules
        # (see can_reexport above) can re-export imports.
        imported_var_names = set(analyzer.ctx.user_imported_variables.keys())
        top_level_decls = analyzer.ctx.top_level_decls
        for name, var_type in analyzer.global_scope.all().items():
            if name == "__name__":  # Don't export synthetic __name__
                continue
            # Don't re-export imported variables unless redefined at top level
            # Exception: package __init__.py files and stdlib facades can re-export.
            is_reexport = name in imported_var_names and name not in top_level_decls
            if is_reexport:
                if not can_reexport:
                    continue
                # Track re-export source for codegen. Flatten through
                # any intervening re-exports so the recorded source is
                # the *ultimate* defining module -- matches the
                # `record_info.defining_module` / `EnumInfo.module_name`
                # pattern records and enums use, and lets the .hpp
                # `using` emission render the definer's qname directly
                # (critical when the immediate source is a native_module
                # facade with no .hpp).
                imm_source, imm_orig = analyzer.ctx.user_imported_variables[name]
                source_module, original_name = self._flatten_var_reexport(
                    imm_source, imm_orig)
                exports.reexported_variables[name] = (source_module, original_name)
            exports.variables[name] = var_type
            if name in analyzer.ctx.final_globals:
                exports.final_variables.add(name)
            elif is_reexport:
                # Re-exported names are never in final_globals (only locally
                # declared Finals are); look through to the source module so
                # downstream sees the original Final marker.
                src_info = analyzer.ctx.registry.get_module(source_module)
                src_var = src_info.variables.get(original_name) if src_info else None
                if src_var is not None and src_var.is_final:
                    exports.final_variables.add(name)

        # Per-module attribute table: backfill any entries the
        # registration paths missed (e.g. macro-emitted records, untyped
        # globals registered during body sema), then assert consistency
        # between the table and `compiled.exports`. No-op when assertions
        # are disabled (-O builds).
        self._backfill_module_attributes(compiled)
        if __debug__:
            self._assert_module_attributes_consistent(compiled)

    def _pre_populate_reexport_bindings(self) -> None:
        """Second pre-pop pass: walk each module's parsed `ast.imports`
        and install bindings for re-exported names by chaining through
        peers' already-pre-populated tables. Required for cycle peers,
        where a member's `bind_imports` may run before another peer has
        had a chance to materialize a re-exported name -- without these
        bindings, the consumer hits "X not found in module Y".

        Iterates to a fixpoint so SCC-internal chains converge.

        Star-import placeholders (`from M import *` leaves
        `ast.imports[M]` empty at parse time) are filled later by
        `_expand_star_imports_for_module`, called per consumer in
        topological order before its `_finalize_declarations`. By that
        point the source module's `module_attributes` has its full
        decl set including variables, which only land in the table
        when their type is resolved during the source's own
        `register_globals` -- earlier than this pass can see them.
        """
        from .symbol_binding import install_binding
        work: list[tuple[dict, dict, set[tuple[str, str]], str]] = []
        for compiled in self.modules.values():
            ast = compiled.ast
            if ast is None or not ast.imports:
                continue
            for src_module, names in ast.imports.items():
                if not isinstance(names, set):
                    continue
                src_compiled = self.modules.get(src_module)
                if src_compiled is None:
                    continue
                work.append((compiled.module_attributes,
                             src_compiled.module_attributes, names, src_module))
        progress = True
        max_iters = len(self.modules) + 2
        while progress and max_iters > 0:
            progress = False
            max_iters -= 1
            for table, src_table, names, src_module in work:
                for original_name, local_name in names:
                    if local_name in table:
                        continue
                    src_cell = src_table.get(original_name)
                    if src_cell is None:
                        continue
                    src_bd = src_cell.binding
                    install_binding(
                        table, local_name, src_bd.kind, src_bd.info,
                        defining_module=src_bd.defining_module or src_module,
                        canonical_name=src_bd.canonical_name,
                    )
                    progress = True

    def _expand_star_imports_for_module(self, compiled: 'CompiledModule') -> None:
        """Expand each `from M import *` placeholder in `compiled.ast`
        by iterating `M`'s per-module attribute table. Filters via
        `_star_exportable` (the `__all__` literal on `M.ast.module_all`
        when set, plus the leading-underscore rule).

        Runs once per consumer in topological compile order, before
        the consumer's `_resolve_types_for_module`. Topological order
        means every non-cycle-peer source `M` has already completed
        `_finalize_declarations`, so `M.module_attributes` is fully
        populated -- including variables, which `register_globals`
        installs only after type resolution. Cycle peers share the
        same limitation as the existing variable / type-alias
        re-export gaps (see BUGS.md).

        Populates both `compiled.ast.imports[M]` (consumed by sema's
        `bind_imports`) and the parser's `_name_index` (consumed by
        the upcoming `resolve_refs` pass for type annotations that
        reference star-imported names)."""
        ast = compiled.ast
        if ast is None or not ast.star_imports:
            return
        for src_module in ast.star_imports:
            # Implicit-stdlib stars (`from tpy/builtins/typing import *`)
            # are expanded at parse time by `ImportProcessor`, so
            # `ast.imports[src_module]` is already populated and the
            # `_name_index` is already routed. Skipping avoids a no-op
            # rescan of `tpy.module_attributes` for every consumer.
            if src_module in _IMPLICIT_MODULES:
                continue
            self._expand_one_star_import(compiled, src_module)

    @staticmethod
    def _star_exportable(name: str, module_all: 'frozenset[str] | None',
                         binding=None) -> bool:
        """`from M import *` filter: True if `name` should land in the
        consumer's namespace. Mirrors CPython: explicit `__all__` wins;
        otherwise the leading-underscore rule applies. Parser keywords
        (the few bootstrap decorator stubs in tpy.extern) never escape
        a star import even when their `__all__` would include them;
        the `binding` parameter is omitted at pass-2 callsites where
        the kind isn't known."""
        if binding is not None and binding.kind == SymbolKind.PARSER_KEYWORD:
            return False
        if module_all is not None:
            return name in module_all
        return not name.startswith("_")

    def _expand_one_star_import(
        self, compiled: 'CompiledModule', src_module: str,
    ) -> None:
        """Fill the `from src_module import *` placeholder in
        `compiled.ast.imports[src_module]`.

        Two passes are needed:
          1. `module_attributes` covers locally-defined records /
             functions / enums / protocols / variables (post-pre-pop
             and post-sema).
          2. `ast.imports` covers parser-keyword / builtin re-exports
             (like `from tpy import Int32`) that have no .py-level
             Info payload and so don't end up in `module_attributes`.

        Both passes target the same `imports[src_module]` bucket, so
        a single `seen_locals` set deduplicates across them. No-op
        when `src_module` is not in `ast.imports` (e.g. stdlib star
        whose names the parser still expands inline, or an external
        module without a CompiledModule)."""
        ast = compiled.ast
        if ast is None or src_module not in ast.imports:
            return
        src_bucket = ast.imports[src_module]
        if not isinstance(src_bucket, set):
            return
        src_compiled = self.modules.get(src_module)
        if src_compiled is None or src_compiled.ast is None:
            return
        src_module_all = src_compiled.ast.module_all
        seen_locals = {local for (_, local) in src_bucket}
        for name, cell in src_compiled.module_attributes.items():
            if name in seen_locals:
                continue
            binding = cell.binding
            if not self._star_exportable(name, src_module_all, binding):
                continue
            self._record_star_imported_name(
                compiled, src_module, name, name, binding)
            seen_locals.add(name)
        for other_src, names in src_compiled.ast.imports.items():
            if not isinstance(names, set):
                continue
            for orig, local in names:
                if local in seen_locals:
                    continue
                if not self._star_exportable(local, src_module_all):
                    continue
                self._record_star_imported_name(
                    compiled, other_src, orig, local, binding=None)
                seen_locals.add(local)

    def _record_star_imported_name(
        self, compiled: 'CompiledModule', src_module: str,
        original_name: str, local_name: str,
        binding,
    ) -> None:
        """Register one star-imported name in the consumer's parsed
        import dict and parser name_index.

        The (name, name) entry always lands in `ast.imports[src_module]`
        so sema's `bind_imports` processes the name through its
        existing star branch (`_register_user_module_import` followed
        by `_bind_star_reexport` on miss). The parser-side
        `_name_index` is rerouted to the ultimate definer when known
        (pass 1 binding) or the public stdlib module (pass 2 builtin
        names) so cross-module canonicalization lands at e.g.
        `tpy.Int32` rather than the private `tpy._core._types.Int32`.
        """
        ast = compiled.ast
        if ast is None:
            return
        bucket = ast.imports.setdefault(src_module, set())
        if isinstance(bucket, set):
            bucket.add((original_name, local_name))
        if ast.resolver is None:
            return
        if binding is not None and binding.defining_module is not None:
            index_module, index_orig = (
                binding.defining_module, binding.canonical_name)
        elif binding is None and (stdlib := route_stdlib_name(original_name)):
            index_module, index_orig = stdlib
        else:
            index_module, index_orig = src_module, original_name
        ast.resolver.index_imported_name(local_name, index_module, index_orig)

    def _check_module_all_completeness(self, compiled: 'CompiledModule') -> None:
        """Warn for names listed in `__all__` that the module does not
        actually export.

        CPython raises `AttributeError: module 'M' has no attribute 'X'`
        at star-import time for the same input; TPy's star expansion
        silently drops phantom names, which is a typo trap. A warning
        (rather than error) matches the project's tone for stylistic
        gaps and avoids breaking users with pre-existing typos.

        Runs after `_finalize_declarations` so `module_attributes`
        carries every local def (records / functions / enums /
        protocols / variables / type aliases). The check fires
        unconditionally on every module that defines `__all__`,
        whether or not anyone star-imports it -- parallels the
        parse-time check for non-literal `__all__`.
        """
        ast = compiled.ast
        if ast is None or ast.module_all is None or compiled.analyzer is None:
            return
        # Implicit stdlib modules (tpy / builtins / typing) legitimately
        # list parser-keyword and primitive names in `__all__` that have
        # no `def` / `class` / import surface (e.g. `None`, `tuple`,
        # `auto_own`). Those names route through the parser's keyword
        # table, not `module_attributes`, so the surface check would
        # produce false positives. The check is meant to catch user
        # typos, not police stdlib bootstrap.
        if compiled.name in _IMPLICIT_MODULES:
            return
        surface: set[str] = set(compiled.module_attributes)
        surface.update(ast.module_aliases.values())
        # `import X` (bare module imports) brings `X` into the module's
        # namespace as a submodule binding; not in `module_attributes`
        # or `module_aliases`. Without this union, a module that re-
        # exports a bare-imported submodule via `__all__ = ["X"]`
        # would get a false phantom warning.
        surface.update(ast.bare_module_imports)
        for names in ast.imports.values():
            if isinstance(names, set):
                for _, local in names:
                    surface.add(local)
        phantoms = sorted(ast.module_all - surface)
        # Emit on the source module's analyzer so the diagnostic carries
        # the source filename in formatted output, matching where the
        # `__all__` literal physically lives. Using compiler-level
        # diagnostics would attribute the warning to the entry point.
        for name in phantoms:
            compiled.analyzer.ctx.warning_from_loc(
                f"__all__ lists '{name}' which is not defined in this module",
                ast.module_all_loc,
            )

    def _flatten_var_reexport(self, source_module: str,
                               original_name: str) -> tuple[str, str]:
        """Walk `source_module.exports.reexported_variables` until reaching
        the ultimate defining module. Returns the (definer_module,
        definer_name) tuple. Idempotent across cycles (returns the last
        seen tuple if a chain loops). Used by `_extract_body_exports`
        to flatten variable re-export chains so the consumer's .hpp
        renders the definer's qname directly -- mirrors what records
        do via `record_info.defining_module`.
        """
        visited: set[tuple[str, str]] = set()
        cur_mod, cur_name = source_module, original_name
        while True:
            key = (cur_mod, cur_name)
            if key in visited:
                return (cur_mod, cur_name)
            visited.add(key)
            cur_compiled = self.modules.get(cur_mod)
            if cur_compiled is None:
                return (cur_mod, cur_name)
            next_step = cur_compiled.exports.reexported_variables.get(cur_name)
            if next_step is None:
                return (cur_mod, cur_name)
            cur_mod, cur_name = next_step

    def _backfill_module_attributes(self, compiled: CompiledModule) -> None:
        """Install bindings for every entry in `compiled.exports.{records,
        functions,protocols,enums,variables,type_aliases}` that doesn't
        already have one. Skip-if-present so the registration paths'
        bindings (which know the immediate import source for re-exports)
        are not clobbered by the more-naive "is it in
        reexported_records?" attribution this backfill computes.

        The incremental population in `_pre_populate_decl_exports`,
        `_register_user_module_import`, and the registration helpers
        covers the common case; this backfill catches the corners
        (untyped globals registered during body sema, macro-emitted
        records, builtin records, facade re-exports of protocols
        which have no `reexported_protocols` tracker). Future phases
        push more population upstream and shrink this method.
        """
        table = compiled.module_attributes
        exports = compiled.exports
        for name, info in exports.records.items():
            if name in table:
                continue
            reexport = exports.reexported_records.get(name)
            install_binding(
                table, name, SymbolKind.RECORD, info,
                defining_module=(reexport[0] if reexport else None),
                canonical_name=(reexport[1] if reexport else None),
            )
        for name, fi_list in exports.functions.items():
            if name in table:
                continue
            reexport = exports.reexported_functions.get(name)
            install_binding(
                table, name, SymbolKind.FUNCTION, fi_list,
                defining_module=(reexport[0] if reexport else None),
                canonical_name=(reexport[1] if reexport else None),
            )
        for name, pinfo in exports.protocols.items():
            if name in table:
                continue
            kind = protocol_kind_for(pinfo.is_dynamic)
            install_binding(
                table, name, kind, pinfo,
            )
        for name, etype in exports.enums.items():
            if name in table:
                continue
            reexport = exports.reexported_enums.get(name)
            install_binding(
                table, name, SymbolKind.ENUM, etype,
                defining_module=(reexport[0] if reexport else None),
                canonical_name=(reexport[1] if reexport else None),
            )
        for name, vtype in exports.variables.items():
            if name in table:
                continue
            reexport = exports.reexported_variables.get(name)
            install_binding(
                table, name, SymbolKind.VARIABLE, vtype,
                defining_module=(reexport[0] if reexport else None),
                canonical_name=(reexport[1] if reexport else None),
            )
        for name, alias_type in exports.type_aliases.items():
            if name in table:
                continue
            install_binding(
                table, name, SymbolKind.TYPE_ALIAS, alias_type,
            )

    def _assert_module_attributes_consistent(self, compiled: CompiledModule) -> None:
        """Cross-check `compiled.module_attributes` against
        `compiled.exports`. Forward direction only (every export has a
        matching cell); the table may carry extra entries for imports
        that this module doesn't re-export.

        Identity check on `binding.info`. The `defining_module`
        attribution can come from one of two sources -- the
        `reexported_*` tracker dict (for records/enums/functions/
        variables on facade modules) or the import-path's
        `_register_user_module_import` install (for protocols, which
        have no `reexported_protocols` tracker). The check verifies
        consistency with whichever source has the entry, and accepts
        either None (locally defined) or the immediate import source
        (re-export) when neither tracker is authoritative.
        """
        table = compiled.module_attributes
        exports = compiled.exports
        analyzer = compiled.analyzer
        ctx_user_funcs = analyzer.ctx.user_imported_functions if analyzer else {}
        ctx_user_protos = analyzer.ctx.user_imported_protocols if analyzer else {}
        ctx_user_vars = analyzer.ctx.user_imported_variables if analyzer else {}
        kind_map = (
            (SymbolKind.RECORD, exports.records, exports.reexported_records, None),
            (SymbolKind.FUNCTION, exports.functions, exports.reexported_functions, ctx_user_funcs),
            (SymbolKind.ENUM, exports.enums, exports.reexported_enums, None),
            (SymbolKind.VARIABLE, exports.variables, exports.reexported_variables, ctx_user_vars),
        )
        for expected_kind, exports_dict, reexport_dict, ctx_dict in kind_map:
            for name, info in exports_dict.items():
                cell = table.get(name)
                assert cell is not None, (
                    f"{compiled.name}: missing module_attributes entry "
                    f"for {expected_kind.name} '{name}'"
                )
                # Source-order conflict resolution may have replaced an
                # earlier kind with a later one (CPython "last
                # module-scope binding wins"). Phase 1 doesn't enforce
                # the kind alignment in that case -- accept any.
                bd = cell.binding
                if bd.kind != expected_kind:
                    continue
                assert bd.info is info, (
                    f"{compiled.name}: module_attributes['{name}'].info "
                    f"identity mismatch with exports.{expected_kind.name.lower()}s"
                )
                self._assert_binding_attribution(
                    compiled.name, name, bd, reexport_dict, ctx_dict,
                )
        # Protocols: identity check + attribution from
        # user_imported_protocols (no reexported_protocols tracker).
        for name, pinfo in exports.protocols.items():
            cell = table.get(name)
            assert cell is not None, (
                f"{compiled.name}: missing module_attributes entry "
                f"for protocol '{name}'"
            )
            bd = cell.binding
            if bd.kind not in (SymbolKind.PROTOCOL_STATIC,
                               SymbolKind.PROTOCOL_DYNAMIC):
                continue
            assert bd.info is pinfo, (
                f"{compiled.name}: protocol '{name}' info identity mismatch"
            )
            self._assert_binding_attribution(
                compiled.name, name, bd, {}, ctx_user_protos,
            )

    def _assert_binding_attribution(
        self, mod_name: str, name: str, bd, reexport_dict: dict, ctx_dict: 'dict | None',
    ) -> None:
        """Verify (defining_module, canonical_name) on `bd` against
        whichever attribution source is authoritative for this name.

        - If `reexport_dict[name]` exists: binding must match exactly.
        - Else if binding claims a defining_module AND `ctx_dict[name]`
          exists: binding must match the import tuple.
        - Else: no attribution constraint (local definitions and
          source-order shadows both have defining_module=None and a
          ctx_dict entry that may be stale).

        Phase 2 cross-check; Phase 4 (universal re-export) tightens
        this once `reexported_protocols` and per-kind dict cleanup
        on local-shadow give us a single authoritative source.
        """
        reexport = reexport_dict.get(name)
        if reexport is not None:
            src, orig = reexport
            assert bd.defining_module == src, (
                f"{mod_name}: '{name}' defining_module "
                f"{bd.defining_module!r} != reexport src {src!r}"
            )
            assert bd.canonical_name == orig, (
                f"{mod_name}: '{name}' canonical_name "
                f"{bd.canonical_name!r} != reexport orig {orig!r}"
            )
            return
        if bd.defining_module is not None and ctx_dict is not None and name in ctx_dict:
            src, orig = ctx_dict[name]
            assert bd.defining_module == src, (
                f"{mod_name}: '{name}' defining_module "
                f"{bd.defining_module!r} != imported src {src!r}"
            )
            assert bd.canonical_name == orig, (
                f"{mod_name}: '{name}' canonical_name "
                f"{bd.canonical_name!r} != imported orig {orig!r}"
            )

    def _exports_to_module_info(self, name: str, exports: ModuleExports,
                               compiled: 'CompiledModule | None' = None) -> 'ModuleInfo':
        """Convert ModuleExports to ModuleInfo for unified registry storage.

        Args:
            name: Module name (may be dotted, e.g., "mypackage.submod").
            exports: The module exports to convert.

        Returns:
            ModuleInfo suitable for registry storage.
        """
        from .typesys import ModuleInfo, ModuleVarInfo, FinalType
        from .parse import TpyVarDecl, VarLinkage

        functions = dict(exports.functions)

        # Collect native_global declarations from this module's top-level
        # statements so use sites in other modules can emit the correct
        # absolute-qualified C++ name instead of the module-namespace-based one.
        native_globals_by_name: dict[str, str] = {}
        if compiled is not None:
            for stmt in compiled.ast.top_level_stmts:
                if isinstance(stmt, TpyVarDecl) and stmt.linkage != VarLinkage.DEFAULT:
                    cpp_name = stmt.native_name or stmt.name
                    native_globals_by_name[stmt.name] = f"::{cpp_name}"

        # cpp_expr is the cross-module use-site form, always absolute
        # (`::tpyapp::...`). Re-exported variables resolve to their
        # ultimate defining module via the attribute-table binding;
        # falls back to exports.reexported_variables for ad-hoc
        # ModuleInfo (no CompiledModule attached).
        from .symbol_binding import lookup_qualified
        attrs = compiled.module_attributes if compiled is not None else None
        variables = {}
        for k, v in exports.variables.items():
            qual = lookup_qualified(attrs, k, name)
            if qual is not None:
                cpp_expr = qualified_cpp_name(*qual)
            elif k in exports.reexported_variables:
                cpp_expr = qualified_cpp_name(*exports.reexported_variables[k])
            else:
                cpp_expr = qualified_cpp_name(name, k)
            # Non-Final non-value-type globals are stored as T* pointers in C++;
            # native_globals are declared directly via `extern T name;` so they
            # don't add the pointer indirection layer regardless of value-type-ness.
            is_native_global = k in native_globals_by_name
            is_ptr = (not isinstance(v, FinalType)
                      and not v.is_value_type()
                      and not is_native_global)
            variables[k] = ModuleVarInfo(
                k, v, cpp_expr, is_pointer=is_ptr,
                native_cpp_name=native_globals_by_name.get(k),
                is_final=k in exports.final_variables,
            )

        is_native = compiled.ast.directives.native_module if compiled else False
        gen_header = not is_native
        includes = list(compiled.ast.directives.includes) if compiled else []
        return ModuleInfo(
            name=name,
            is_builtin=False,
            is_native_module=is_native,
            generates_header=gen_header,
            includes=includes,
            functions=functions,
            variables=variables,
            records=exports.records,
            protocols=exports.protocols,
            type_aliases=exports.type_aliases,
            recursive_union_names=set(exports.recursive_union_names),
            enums=exports.enums,
            reexported_variables=dict(exports.reexported_variables),
            reached=set(exports.reached),
            module_attributes=(compiled.module_attributes if compiled else None),
        )

    def _index_builtin_type_records(self, module_info: 'ModuleInfo',
                                     analyzer: 'SemanticAnalyzer') -> None:
        """Index .py-defined builtin type records by qualified name.

        Sets type_factory on records that need one, populates the qname index
        so get_builtin_record can find these records, and ensures
        extends_protocols includes implemented_protocols.
        """
        for record in module_info.records.values():
            builtin_qname = record.builtin_type_key or f"{module_info.name}.{record.name}"
            if not record.type_factory:
                td = _get_type_def(builtin_qname)
                if td is not None and td.type_factory is not None:
                    record.type_factory = td.type_factory
            for proto in record.implemented_protocols:
                ext_str = str(proto)
                if ext_str not in record.extends_protocols:
                    record.extends_protocols.append(ext_str)
            if record.builtin_type_key:
                analyzer.registry.register_builtin_record(record.builtin_type_key, record)
        # Resolve NominalType self-references in method signatures.
        # When a @builtin_type class references itself in method params/returns
        # (e.g. BytesView.find(sub: BytesView)), the parser creates NominalType
        # because the factory type isn't registered yet. Resolve them now.
        self._resolve_builtin_self_refs(module_info)

    def _resolve_builtin_self_refs(self, module_info: 'ModuleInfo') -> None:
        """Replace NominalType with factory types in builtin record method signatures."""
        from dataclasses import replace as dc_replace
        from .typesys import NominalType
        # Build name -> factory for non-generic builtin types in this module.
        # Generic entries are skipped: self-refs without type_args only make
        # sense for zero-arg factories that resolve to a singleton.
        name_to_factory: dict[str, 'Callable[[], TpyType]'] = {}
        for rec in module_info.records.values():
            btk = rec.builtin_type_key
            if btk:
                td = _get_type_def(btk)
                if td is not None and td.type_factory is not None and not td.param_kinds:
                    name_to_factory[rec.name] = td.type_factory
        if not name_to_factory:
            return
        def resolve(t: TpyType) -> TpyType:
            if isinstance(t, NominalType) and not t.type_args and t.name in name_to_factory:
                return name_to_factory[t.name]()
            return t.map_inner_types(resolve)
        cache = self._resolved_self_ref_records
        for rec in module_info.records.values():
            rec_id = id(rec)
            if rec_id in cache:
                continue
            cache.add(rec_id)
            for methods in rec.methods.values():
                for method in methods:
                    for i, p in enumerate(method.params):
                        resolved = resolve(p.type)
                        if resolved is not p.type:
                            method.params[i] = dc_replace(p, type=resolved)
                    resolved_ret = resolve(method.return_type)
                    if resolved_ret is not method.return_type:
                        method.return_type = resolved_ret

    def _check_no_errors(self, compiled: CompiledModule) -> None:
        """Raise if the module has any error-level diagnostics from analysis."""
        if compiled.analyzer:
            for d in compiled.analyzer.diagnostics:
                if d.level == DiagnosticLevel.ERROR:
                    raise CompileError(
                        f"Cannot generate code: module has errors",
                        module_name=compiled.name, path=compiled.path,
                    )

    def generate_code(self, compiled: CompiledModule, output_dir: Path,
                      entry_module_name: str | None = None,
                      options: CodeGenOptions | None = None,
                      flat: bool = False) -> tuple[Path, Path | None] | tuple[None, None]:
        """Generate C++ code for a compiled module.

        Args:
            compiled: The compiled module.
            output_dir: Root output directory.
            entry_module_name: Name of the entry point module (for root dir naming).
            options: Code generation options (optional).

        Returns:
            Tuple of (hpp_path, cpp_path). cpp_path is None for native_module modules
            (no .cpp is generated for binding-only modules).
        """
        self._check_no_errors(compiled)
        mod_name = compiled.name

        # Determine root directory name from entry module
        if entry_module_name is None:
            for m in self.modules.values():
                if m.is_entry_point:
                    entry_module_name = m.name
                    break
            else:
                entry_module_name = mod_name

        layout = BuildLayout(output_dir, entry_module_name, flat=flat)
        hpp_path = layout.hpp_path(mod_name)
        cpp_path = layout.cpp_path(mod_name)

        assert compiled.analyzer is not None
        codegen = CodeGenerator(compiled.analyzer, options)
        # Pass actual user modules (those in self.modules, not builtins without user files)
        actual_user_modules = set(self.modules.keys())
        implicit_stdlib = self._implicit_stdlib_set()
        # Peer modules in the same SCC. Codegen uses this to swap
        # full <peer>.hpp includes for <peer>_fwd.hpp in the .hpp
        # (cycle-breaking forward declarations) and to emit our own
        # <mod>_fwd.hpp. Empty for non-cycle modules.
        cycle_peers = self._cycle_peers.get(mod_name, frozenset())
        hpp_code, cpp_code = codegen.generate(
            compiled.ast, mod_name,
            is_entry_point=compiled.is_entry_point,
            actual_user_modules=actual_user_modules,
            implicit_stdlib_modules=implicit_stdlib,
            reexported_functions=compiled.exports.reexported_functions,
            reexported_records=compiled.exports.reexported_records,
            reexported_variables=compiled.exports.reexported_variables,
            reexported_enums=compiled.exports.reexported_enums,
            cycle_peers=cycle_peers,
        )

        if not hpp_code:
            return None, None
        hpp_path.parent.mkdir(parents=True, exist_ok=True)
        hpp_path.write_text(hpp_code)
        # Cycle members get a `<mod>_fwd.hpp` so peers can include
        # forward declarations without pulling in full layout.
        if cycle_peers:
            fwd_code = codegen.generate_fwd_header(compiled.ast, mod_name)
            fwd_path = layout.fwd_hpp_path(mod_name)
            fwd_path.parent.mkdir(parents=True, exist_ok=True)
            fwd_path.write_text(fwd_code)
        cpp_path.parent.mkdir(parents=True, exist_ok=True)
        cpp_path.write_text(cpp_code)

        return hpp_path, cpp_path

    def generate_code_to_strings(self, compiled: CompiledModule,
                                  options: CodeGenOptions | None = None) -> tuple[str, str]:
        """Generate C++ code and return as strings (no file I/O)."""
        self._check_no_errors(compiled)
        assert compiled.analyzer is not None
        codegen = CodeGenerator(compiled.analyzer, options)
        actual_user_modules = set(self.modules.keys())
        implicit_stdlib = self._implicit_stdlib_set()
        return codegen.generate(
            compiled.ast, compiled.name,
            is_entry_point=compiled.is_entry_point,
            actual_user_modules=actual_user_modules,
            implicit_stdlib_modules=implicit_stdlib,
            reexported_functions=compiled.exports.reexported_functions,
            reexported_records=compiled.exports.reexported_records,
            reexported_variables=compiled.exports.reexported_variables,
            reexported_enums=compiled.exports.reexported_enums,
        )

    def _propagate_package_directives(self) -> None:
        """Reserved for future package-level directive propagation.

        native_module is no longer propagated -- each module must declare
        it explicitly.
        """
        pass

    def _apply_native_namespace_prefix(self) -> None:
        """Validate native_module contents and auto-prefix @native renames.

        Two passes per module:
          1. For native_module only: validate that only @native/@builtin
             entities are present (declaration-only constraint).
          2. For any module with cpp_namespace: auto-prefix module-level
             @native renames that are either empty or contain no `::`.
             Rule: the rename is treated as relative to cpp_namespace
             unless it contains `::` (any occurrence = absolute opt-out).
             Applies to functions, records, and native_global declarations
             with C++ linkage. C-linkage is skipped (global scope by ABI).
             @builtin_type records are skipped -- their emission goes
             through TypeDef.cpp_formatter and the rename is informational.
        """
        # Note: TpyVarDecl.linkage is populated by sema (statements.py), not
        # the parser, so an auto-prefix loop for native_global here would be
        # dead code. Native globals are intentionally emitted at global C++
        # scope (`::name`) -- they model externally-linked symbols whose C
        # ABI names don't depend on the importing module's cpp_namespace.
        from .parse.nodes import FunctionLinkage as FL, RecordLinkage as RL
        def _needs_prefix(rename: str | None) -> bool:
            return not rename or "::" not in rename
        for compiled in self.modules.values():
            if compiled.ast.directives.native_module:
                for func in compiled.ast.functions:
                    if func.linkage == FL.EXPORT_C:
                        raise CompileError(
                            f"@export not allowed in native_module "
                            f"('{compiled.name}' is declaration-only)",
                            compiled.name, compiled.path,
                            lineno=func.loc.line if func.loc else None)
                    if (func.linkage == FL.DEFAULT
                            and not func.builtin_function_key
                            and not func.builtin_decorator_key
                            and not func.cpp_template):
                        raise CompileError(
                            f"non-native function '{func.name}' not allowed "
                            f"in native_module ('{compiled.name}' is declaration-only)",
                            compiled.name, compiled.path,
                            lineno=func.loc.line if func.loc else None)
                for record in compiled.ast.records:
                    if record.linkage == RL.DEFAULT and not record.builtin_type_key:
                        raise CompileError(
                            f"non-native class '{record.name}' not allowed "
                            f"in native_module ('{compiled.name}' is declaration-only)",
                            compiled.name, compiled.path,
                            lineno=record.loc.line if record.loc else None)
                for proto in compiled.ast.protocols:
                    raise CompileError(
                        f"protocol '{proto.name}' not allowed in "
                        f"native_module ('{compiled.name}' is declaration-only)",
                        compiled.name, compiled.path,
                        lineno=proto.loc.line if proto.loc else None)
            ns = compiled.ast.directives.cpp_namespace
            if not ns:
                continue
            for func in compiled.ast.functions:
                if func.linkage == FL.NATIVE and _needs_prefix(func.native_name):
                    func.native_name = f"{ns}::{func.native_name or func.name}"
            for record in compiled.ast.records:
                if (record.linkage == RL.NATIVE
                        and record.builtin_type_key is None
                        and _needs_prefix(record.native_name)):
                    record.native_name = f"{ns}::{record.native_name or record.name}"

    def _build_namespace_map(self) -> dict[str, str]:
        """Build module_name -> C++ namespace mapping from # tpy: namespace directives.

        Rules:
        - Direct directive: module uses the specified namespace as-is.
        - Package inheritance: if __init__.py has a directive, child modules
          use "parent_ns::relative_child" (most-specific parent wins).
        - No directive: default "tpyapp::module_name".
        """
        # Collect direct namespace overrides
        direct: dict[str, str] = {}
        # Package-level overrides (from __init__.py) propagate to children
        package_overrides: dict[str, str] = {}
        for name, compiled in self.modules.items():
            ns = compiled.ast.directives.cpp_namespace
            if ns is not None:
                direct[name] = ns
                if compiled.is_package_init:
                    package_overrides[name] = ns

        ns_map: dict[str, str] = {}
        for name in self.modules:
            if name in direct:
                ns_map[name] = direct[name]
                continue

            # Walk up parent packages (most specific __init__.py first)
            parts = name.split('.')
            resolved = None
            for i in range(len(parts) - 1, 0, -1):
                parent = '.'.join(parts[:i])
                if parent in package_overrides:
                    relative = '::'.join(parts[i:])
                    resolved = f"{package_overrides[parent]}::{relative}"
                    break

            if resolved:
                ns_map[name] = resolved
            else:
                ns_map[name] = f"tpyapp::{name.replace('.', '::')}"
                # Warn if a library module has no namespace override
                compiled = self.modules[name]
                if self._is_lib_module(compiled):
                    self.diagnostics.append(Diagnostic(
                        DiagnosticLevel.WARNING,
                        f"library module '{name}' has no # tpy: cpp_namespace "
                        f"directive (will use default '{ns_map[name]}')"))

        return ns_map

    def _is_lib_module(self, compiled: 'CompiledModule') -> bool:
        """Check if a module comes from a library search path (not user code)."""
        if not self.resolver or not compiled.path.is_absolute():
            return False
        for lib_dir in self.resolver.extra_dirs:
            try:
                compiled.path.relative_to(lib_dir)
                return True
            except ValueError:
                continue
        return False

    def _build_include_path_map(self, ns_map: dict[str, str]) -> dict[str, str]:
        """Build module_name -> include path mapping.

        For package __init__ modules with inherited cpp_namespace, derives
        from namespace (e.g. "tpystd::tpy" -> "tpystd/tpy.hpp").
        For private (_-prefixed) submodules with explicit cpp_namespace, uses
        namespace_dir/_leaf.hpp to stay in the same directory tree.
        All other modules use the default module-name-based path.
        """
        ip_map: dict[str, str] = {}
        for name, compiled in self.modules.items():
            # Private submodules with explicit cpp_namespace (sharing their
            # parent's C++ namespace) use namespace_dir/leaf_name.hpp to avoid
            # colliding with the parent's header while staying in the same
            # directory tree (e.g. tpystd::tpy + _types -> tpystd/tpy/_types.hpp).
            if compiled.ast.directives.cpp_namespace is not None:
                leaf = name.rsplit('.', 1)[-1]
                if leaf.startswith('_'):
                    ns = ns_map[name]
                    ip_map[name] = ns.replace('::', '/') + '/' + leaf + '.hpp'
                    continue
            # Derive from namespace if it differs from the default.
            ns = ns_map[name]
            default_ns = f"tpyapp::{name.replace('.', '::')}"
            if ns != default_ns:
                ip_map[name] = ns.replace('::', '/') + '.hpp'

        # Detect and fix include path collisions: when multiple modules map
        # to the same header (e.g. shared cpp_namespace), disambiguate by
        # appending the module leaf name.
        # Note: module_to_include_path() reads the global _include_path_map
        # which isn't set yet -- it falls through to the name-based default,
        # which is the correct fallback for modules not in ip_map.
        seen: dict[str, str] = {}
        collisions: set[str] = set()
        for name in self.modules:
            path = ip_map.get(name) or module_to_include_path(name)
            if path in seen:
                collisions.add(path)
            seen[path] = name

        for collision_path in collisions:
            for name in self.modules:
                path = ip_map.get(name) or module_to_include_path(name)
                if path == collision_path and name in ip_map:
                    leaf = name.rsplit('.', 1)[-1]
                    base = collision_path.removesuffix('.hpp')
                    new_path = base + '/' + leaf + '.hpp'
                    if new_path in seen and seen[new_path] != name:
                        self.diagnostics.append(Diagnostic(
                            DiagnosticLevel.WARNING,
                            f"include path collision: '{name}' maps to "
                            f"'{new_path}' which is already used by "
                            f"'{seen[new_path]}'"))
                    ip_map[name] = new_path

        return ip_map

    def collect_link_flags(self) -> list[str]:
        """Collect -l linker flags from all compiled modules' link directives.

        Filters by current platform. Deduplicates while preserving order.
        """
        seen: set[str] = set()
        flags: list[str] = []
        for compiled in self.modules.values():
            for lib, platform_filter in compiled.ast.directives.link_libs:
                if platform_filter is not None:
                    mapped = _PLATFORM_MAP.get(platform_filter.lower(), platform_filter)
                    if not sys.platform.startswith(mapped):
                        continue
                flag = f"-l{lib}"
                if flag not in seen:
                    seen.add(flag)
                    flags.append(flag)
        return flags

    def collect_third_party_deps(self) -> list[str]:
        """Collect third-party library names declared by `# tpy: link_third_party`.

        Filters by current platform, deduplicates while preserving order.
        Names map to entries in ``tpyc.build.third_party._FACTORIES``.
        """
        seen: set[str] = set()
        names: list[str] = []
        for compiled in self.modules.values():
            for name, platform_filter in compiled.ast.directives.third_party_deps:
                if platform_filter is not None:
                    mapped = _PLATFORM_MAP.get(
                        platform_filter.lower(), platform_filter)
                    if not sys.platform.startswith(mapped):
                        continue
                if name not in seen:
                    seen.add(name)
                    names.append(name)
        return names
