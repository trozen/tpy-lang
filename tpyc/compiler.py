"""
TurboPython Multi-Module Compiler

Orchestrates compilation of multiple modules, handling:
- Module discovery and dependency resolution
- Circular import detection
- Compilation in dependency order
- Export tracking for cross-module references
"""

from __future__ import annotations
import glob
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .parse import Parser, ParseError, TpyModule, TpyImport, RelativeImportKey, SourceLocation, scan_star_exports
from .parse.imports import StarImportResolver, NonLiteralAllError
from .sema import SemanticAnalyzer, SemanticError, Diagnostic, DiagnosticLevel
from .modules.resolver import ModuleResolver, ResolvedModule
from .modules import get_builtin_module_names, get_type_factory as _get_type_factory
from .modules.type_resolution import _get_type_factories
from .codegen_cpp import CodeGenerator, CodeGenOptions
from .codegen_cpp.context import module_to_cpp_namespace, set_namespace_map, set_include_path_map, get_include_path, clear_namespace_map, module_to_include_path
from .typesys import TpyType, INT32, INT64, BIGINT, clear_all_compilation_state
from .macro_loader import MacroRegistry, is_macro_module_source

if TYPE_CHECKING:
    from .typesys import FunctionInfo, RecordInfo, ProtocolInfo, EnumType, ModuleInfo, ModuleVarInfo


DEFAULT_INT_CHOICES = ("Int32", "Int64", "BigInt")

# Maps user-facing platform names in # tpy: link() to sys.platform prefixes
_PLATFORM_MAP = {"windows": "win32", "linux": "linux", "macos": "darwin"}


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
    import subprocess

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


@dataclass
class CppCompilerConfig:
    """Configuration for the C++ compiler used to build generated code."""
    compiler: list[str] = field(default_factory=lambda: ["g++"])
    std: str = "c++23"
    extra_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    ccache: bool = False
    # TODO: enable stricter warnings once generated code is clean. Blocked on:
    # - exhaustive match/finally/with codegen not emitting __builtin_unreachable() at end labels (return-type)
    # - INT64_MIN/UINT64_MAX emitted as bare integer literals (large-integer-constant)
    # - native global codegen emitting extern + initializer together (extern-initialized)
    # Candidate set: -Werror -Wall -Wextra -Wsign-conversion -Wnull-dereference
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
        override = get_include_path(module_name)
        if override is not None:
            return self.include_dir / override
        parts = module_name.split('.')
        if len(parts) == 1:
            return self.include_dir / f"{parts[0]}.hpp"
        return self.include_dir / Path(*parts[:-1]) / f"{parts[-1]}.hpp"

    def cpp_path(self, module_name: str) -> Path:
        """Path to the generated source for a module."""
        override = get_include_path(module_name)
        if override is not None:
            return self.src_dir / (override.removesuffix('.hpp') + '.cpp')
        parts = module_name.split('.')
        if len(parts) == 1:
            return self.src_dir / f"{parts[0]}.cpp"
        return self.src_dir / Path(*parts[:-1]) / f"{parts[-1]}.cpp"

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
        cmds.append([
            *config.compiler,
            "-o", str(output),
            *obj_files,
            *(extra_objects or []),
            *config.link_flags,
        ])
        return cmds

    def generate_cmake(
        self,
        runtime_include_dir: Path,
        cpp_files: list[Path],
        link_flags: list[str] | None = None,
        extra_include_dirs: list[Path] | None = None,
        bundle_runtime: bool = True,
    ) -> Path:
        """Generate a .cmake include file with source/include/link variables.

        Produces sources.cmake that sets:
          TPYC_SOURCES      -- list of generated .cpp files
          TPYC_INCLUDE_DIRS -- include directories (generated headers + runtime)
          TPYC_LIBRARIES    -- link libraries (from # tpy: link() directives)
          TPYC_CXX_STANDARD -- required C++ standard (23)

        When bundle_runtime is True (default), the runtime headers are copied
        into the output directory so the result is self-contained.

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

        if bundle_runtime:
            bundled_dir = self.root_dir / "runtime" / "include"
            if bundled_dir.exists():
                shutil.rmtree(bundled_dir)
            shutil.copytree(runtime_include_dir, bundled_dir)
            include_dirs.append(f"{cmake_dir}/runtime/include")
        else:
            try:
                include_dirs.append(f"{cmake_dir}/{os.path.relpath(runtime_include_dir, self.root_dir)}")
            except ValueError:
                include_dirs.append(str(runtime_include_dir))

        for d in (extra_include_dirs or []):
            try:
                include_dirs.append(f"{cmake_dir}/{os.path.relpath(d, self.root_dir)}")
            except ValueError:
                include_dirs.append(str(d))

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
    enums: dict[str, 'EnumType'] = field(default_factory=dict)
    variables: dict[str, TpyType] = field(default_factory=dict)
    type_aliases: dict[str, TpyType] = field(default_factory=dict)
    reexported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_enums: dict[str, tuple[str, str]] = field(default_factory=dict)


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
        self.resolver = ModuleResolver(self.entry_point.parent,
                                       extra_dirs=lib_dirs or [])
        self.default_int_type = parse_default_int_type(default_int)
        self._init_shared()

    def _init_shared(self) -> None:
        """Initialize state shared by both file and stdin compilation paths."""
        self.modules: dict[str, CompiledModule] = {}
        self.compile_order: list[str] = []
        self.shadowed_builtins: dict[str, set[tuple[str, int]]] = {}
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

            resolver_fn = self._make_star_import_resolver() if self.resolver else None
            parser = Parser(decorator_schemas=self._decorator_schemas,
                            star_import_resolver=resolver_fn)
            ast = parser.parse(source, module_name=entry_name)
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
            for name in self.compile_order:
                self._analyze_module(self.modules[name])
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

        # 5. Parse and analyze in dependency order
        for module_name in self.compile_order:
            compiled = self.modules[module_name]
            self._analyze_module(compiled)

        # Return in dependency order
        return [self.modules[name] for name in self.compile_order]

    # Stdlib modules that are always compiled (even without explicit import).
    # These provide protocol definitions used by builtins (e.g. Sized for len()).
    _IMPLICIT_STDLIB = ["typing", "tpy", "builtins"]

    def _implicit_stdlib_set(self) -> set[str]:
        """Return the set of implicit stdlib modules including submodules."""
        return self._expand_implicit_prefixes(self.modules)

    @classmethod
    def _expand_implicit_prefixes(cls, names: dict[str, object] | set[str]) -> set[str]:
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

    def _make_star_import_resolver(self) -> StarImportResolver:
        """Create a callback for resolving star import exports via ModuleResolver."""
        cache: dict[str, frozenset[str]] = {}

        def resolver(module_name: str) -> frozenset[str] | None:
            if not self.resolver:
                return None
            resolved = self.resolver.resolve(module_name)
            if resolved is None:
                return None
            key = str(resolved.path)
            if key not in cache:
                source = resolved.path.read_text()
                try:
                    cache[key] = scan_star_exports(source)
                except NonLiteralAllError:
                    raise ParseError(
                        f"'from {module_name} import *': __all__ in '{module_name}' "
                        f"is not a compile-time literal")
            return cache[key]

        return resolver

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
            CompileError: If circular import detected or module not found.
        """
        # Check for circular imports
        if module_name in import_chain:
            cycle = " -> ".join(import_chain + [module_name])
            importing_module = import_chain[-1] if import_chain else module_name
            importing_path = self.modules[importing_module].path if importing_module in self.modules else path
            raise CompileError(f"Circular import detected: {cycle}", importing_module, importing_path,
                               lineno=import_lineno)

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
        parser = Parser(decorator_schemas=self._decorator_schemas,
                        star_import_resolver=self._make_star_import_resolver())
        try:
            ast = parser.parse(source, module_name=module_name,
                               is_package_init=is_package_init)
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
        import_lineno: int,
        module_name: str,
        path: Path,
        builtin_names: set[str],
        new_chain: list[str],
        parent_ast: TpyModule | None = None,
    ) -> None:
        """Resolve a user module import, checking for not-found and builtin shadowing."""
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
        parts = dotted_name.split('.')
        for i in range(1, len(parts)):
            package = '.'.join(parts[:i])
            if package not in self.modules:
                resolved = self.resolver.resolve(package)
                if resolved and resolved.is_package_init:
                    self._discover_modules(package, resolved.path, import_chain, import_lineno,
                                           is_package_init=True)

    def _compute_compile_order(self) -> None:
        """Compute topological sort of modules (dependencies first).

        Uses Kahn's algorithm for deterministic ordering.
        """
        # Build dependency graph
        in_degree: dict[str, int] = {name: 0 for name in self.modules}
        dependents: dict[str, list[str]] = {name: [] for name in self.modules}

        for name, compiled in self.modules.items():
            for dep_name in compiled.ast.user_module_imports:
                if dep_name in self.modules:
                    in_degree[name] += 1
                    dependents[dep_name].append(name)

        # Start with modules that have no dependencies
        queue = [name for name, degree in in_degree.items() if degree == 0]
        queue.sort()  # Deterministic ordering

        result = []
        while queue:
            current = queue.pop(0)
            result.append(current)

            for dependent in dependents[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    # Insert in sorted order for determinism
                    inserted = False
                    for i, name in enumerate(queue):
                        if dependent < name:
                            queue.insert(i, dependent)
                            inserted = True
                            break
                    if not inserted:
                        queue.append(dependent)

        if len(result) != len(self.modules):
            # Should not happen since we check for cycles earlier
            raise CompileError("Internal error: could not resolve module dependencies")

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

    def _analyze_module(self, compiled: CompiledModule) -> None:
        """Analyze a single module.

        Args:
            compiled: The compiled module to analyze.
        """
        # Create analyzer
        analyzer = SemanticAnalyzer(default_int_type=self.default_int_type)
        analyzer.ctx.macro_registry = self._macro_registry
        analyzer.ctx.allow_top_level_error_unwrap = self.allow_top_level_error_unwrap

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

        # Analyze the module
        try:
            analyzer.analyze(
                compiled.ast,
                module_name=module_name,
            )
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
                    from .parse import SourceLocation
                    from .sema.diagnostics import Diagnostic, DiagnosticLevel
                    analyzer.ctx.diagnostics.append(Diagnostic(
                        DiagnosticLevel.WARNING,
                        f"import '{shadowed_name}' shadows builtin module",
                        SourceLocation(lineno, 0)
                    ))

        # Store analyzer for codegen
        compiled.analyzer = analyzer

        # Extract exports from the analyzed module
        self._extract_exports(compiled, analyzer)

    def _extract_exports(self, compiled: CompiledModule, analyzer: SemanticAnalyzer) -> None:
        """Extract exports from an analyzed module.

        Args:
            compiled: The compiled module to update.
            analyzer: The semantic analyzer with registered items.
        """
        exports = compiled.exports
        # Package inits and implicit stdlib facade modules can re-export
        # imported symbols (functions, records, protocols, enums) from other
        # modules. The stdlib extension is safe because native re-exports are
        # filtered out in codegen (no spurious using-declarations).
        can_reexport = compiled.is_package_init or compiled.name in self._implicit_stdlib_set()

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

        # Re-export imported functions from user modules
        if can_reexport:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_functions.items():
                if local_name not in exports.functions:
                    # Get the function info from the source module
                    module_info = analyzer.registry.get_module(source_module)
                    if module_info and original_name in module_info.functions:
                        func_infos = module_info.functions[original_name]
                        if func_infos:
                            exports.functions[local_name] = func_infos
                            # Track re-export source for codegen (skip special_handling
                            # functions like native_global -- they're compiler directives,
                            # not real functions that need C++ declarations)
                            if not func_infos[0].special_handling:
                                exports.reexported_functions[local_name] = (source_module, original_name)

        # Export all user-defined records (including nested)
        for record in compiled.ast.all_records():
            record_info = analyzer.registry.get_record(record.name)
            if record_info:
                exports.records[record.name] = record_info

        # Re-export imported records from user modules
        if can_reexport:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_records.items():
                if local_name not in exports.records:
                    # Get the record info from the source module
                    module_info = analyzer.registry.get_module(source_module)
                    if module_info and original_name in module_info.records:
                        exports.records[local_name] = module_info.records[original_name]
                        # Track re-export source for codegen
                        exports.reexported_records[local_name] = (source_module, original_name)

        # Export all user-defined protocols
        for protocol in compiled.ast.protocols:
            protocol_info = analyzer.registry.get_protocol(protocol.name)
            if protocol_info:
                exports.protocols[protocol.name] = protocol_info

        # Re-export imported protocols from user modules
        if can_reexport:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_protocols.items():
                if local_name not in exports.protocols:
                    # Get the protocol info from the source module
                    module_info = analyzer.registry.get_module(source_module)
                    if module_info and original_name in module_info.protocols:
                        exports.protocols[local_name] = module_info.protocols[original_name]

        # Export all user-defined enums (including nested)
        for enum in compiled.ast.all_enums():
            enum_type = analyzer.registry.get_enum(enum.name)
            if enum_type:
                exports.enums[enum.name] = enum_type

        # Re-export imported enums from user modules
        if can_reexport:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_enums.items():
                if local_name not in exports.enums:
                    module_info = analyzer.registry.get_module(source_module)
                    if module_info and original_name in module_info.enums:
                        exports.enums[local_name] = module_info.enums[original_name]
                        exports.reexported_enums[local_name] = (source_module, original_name)

        # Export type aliases
        for name, (typ, _loc) in compiled.ast.type_aliases.items():
            exports.type_aliases[name] = typ

        # Export global variables (from top-level statements)
        # These are tracked in the global scope, but we must exclude imported variables
        # UNLESS they were redefined at the top level (in top_level_decls)
        # Exception: __init__.py files can re-export imports (Python package semantics)
        imported_var_names = set(analyzer.ctx.user_imported_variables.keys())
        top_level_decls = analyzer.ctx.top_level_decls
        for name, var_type in analyzer.global_scope.all().items():
            if name == "__name__":  # Don't export synthetic __name__
                continue
            # Don't re-export imported variables unless redefined at top level
            # Exception: __init__.py files can re-export for package-level access
            is_reexport = name in imported_var_names and name not in top_level_decls
            if is_reexport:
                if not compiled.is_package_init:
                    continue
                # Track re-export source for codegen
                source_module, original_name = analyzer.ctx.user_imported_variables[name]
                exports.reexported_variables[name] = (source_module, original_name)
            exports.variables[name] = var_type

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

        functions = dict(exports.functions)

        # Create ModuleVarInfo with generated cpp_expr
        # For re-exported variables, use the source module's namespace
        ns = module_to_cpp_namespace(name)
        variables = {}
        for k, v in exports.variables.items():
            if k in exports.reexported_variables:
                source_module, original_name = exports.reexported_variables[k]
                cpp_expr = f"{module_to_cpp_namespace(source_module)}::{original_name}"
            else:
                cpp_expr = f"{ns}::{k}"
            # Non-Final non-value-type globals are stored as T* pointers in C++
            is_ptr = not isinstance(v, FinalType) and not v.is_value_type()
            variables[k] = ModuleVarInfo(k, v, cpp_expr, is_pointer=is_ptr)

        is_native = compiled.ast.directives.native_module if compiled else False
        gen_header = True
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
            enums=exports.enums,
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
                factory = _get_type_factory(builtin_qname)
                if factory:
                    record.type_factory = factory
            for proto in record.implemented_protocols:
                ext_str = str(proto)
                if ext_str not in record.extends_protocols:
                    record.extends_protocols.append(ext_str)
            if record.builtin_type_key:
                analyzer.registry.register_builtin_record(record.builtin_type_key, record)
        # Resolve NamedType self-references in method signatures.
        # When a @builtin_type class references itself in method params/returns
        # (e.g. BytesView.find(sub: BytesView)), the parser creates NamedType
        # because the factory type isn't registered yet. Resolve them now.
        self._resolve_builtin_self_refs(module_info)

    def _resolve_builtin_self_refs(self, module_info: 'ModuleInfo') -> None:
        """Replace NamedType with factory types in builtin record method signatures."""
        from dataclasses import replace as dc_replace
        from .typesys import NamedType
        factories = _get_type_factories()
        # Build name -> factory for non-generic builtin types in this module
        name_to_factory: dict[str, 'Callable[[], TpyType]'] = {}
        for rec in module_info.records.values():
            btk = rec.builtin_type_key
            if btk:
                entry = factories.get(btk)
                if entry and not entry[0]:  # non-generic (no type params)
                    name_to_factory[rec.name] = entry[1]
        if not name_to_factory:
            return
        def resolve(t: TpyType) -> TpyType:
            if isinstance(t, NamedType) and not t.type_args and t.name in name_to_factory:
                return name_to_factory[t.name]()
            return t.map_inner_types(resolve)
        for rec in module_info.records.values():
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
                      flat: bool = False) -> tuple[Path, Path | None]:
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

        codegen = CodeGenerator(compiled.analyzer, options)
        # Pass actual user modules (those in self.modules, not builtins without user files)
        actual_user_modules = set(self.modules.keys())
        implicit_stdlib = self._implicit_stdlib_set()
        hpp_code, cpp_code = codegen.generate(
            compiled.ast, mod_name,
            is_entry_point=compiled.is_entry_point,
            actual_user_modules=actual_user_modules,
            implicit_stdlib_modules=implicit_stdlib,
            reexported_functions=compiled.exports.reexported_functions,
            reexported_records=compiled.exports.reexported_records,
            reexported_variables=compiled.exports.reexported_variables,
            reexported_enums=compiled.exports.reexported_enums
        )

        if not hpp_code:
            # Pure native module with no output -- don't write files
            return None, None
        hpp_path.parent.mkdir(parents=True, exist_ok=True)
        hpp_path.write_text(hpp_code)
        if compiled.ast.directives.native_module:
            return hpp_path, None
        cpp_path.parent.mkdir(parents=True, exist_ok=True)
        cpp_path.write_text(cpp_code)

        return hpp_path, cpp_path

    def generate_code_to_strings(self, compiled: CompiledModule,
                                  options: CodeGenOptions | None = None) -> tuple[str, str]:
        """Generate C++ code and return as strings (no file I/O)."""
        self._check_no_errors(compiled)
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
            reexported_enums=compiled.exports.reexported_enums
        )

    def _propagate_package_directives(self) -> None:
        """Reserved for future package-level directive propagation.

        native_module is no longer propagated -- each module must declare
        it explicitly.
        """
        pass

    def _apply_native_namespace_prefix(self) -> None:
        """Auto-prefix bare @native entities with cpp_namespace.

        When a native_module has cpp_namespace set and a @native entity has no
        explicit native_name, sets native_name to "ns::python_name".
        """
        from .parse.nodes import FunctionLinkage as FL, RecordLinkage as RL, VarLinkage
        for compiled in self.modules.values():
            if not compiled.ast.directives.native_module:
                continue
            # Validate: @export not allowed in native_module
            for func in compiled.ast.functions:
                if func.linkage == FL.EXPORT_C:
                    raise CompileError(
                        f"@export not allowed in native_module "
                        f"('{compiled.name}' is declaration-only)",
                        compiled.name, compiled.path,
                        lineno=func.loc.line if func.loc else None)
            ns = compiled.ast.directives.cpp_namespace
            if not ns:
                continue
            # Auto-prefix bare @native functions (C++ linkage only --
            # C-linkage symbols can't have namespace-qualified names)
            for func in compiled.ast.functions:
                if func.linkage == FL.NATIVE and not func.native_name:
                    func.native_name = f"{ns}::{func.name}"
            # Auto-prefix bare @native records (C++ only)
            for record in compiled.ast.records:
                if record.linkage == RL.NATIVE and not record.native_name:
                    record.native_name = f"{ns}::{record.name}"
            # Auto-prefix bare native_global() declarations (C++ only)
            for stmt in (compiled.ast.top_level_stmts or []):
                if hasattr(stmt, 'linkage') and hasattr(stmt, 'native_name'):
                    if stmt.linkage == VarLinkage.NATIVE and not stmt.native_name:
                        stmt.native_name = f"{ns}::{stmt.name}"

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
