"""
TurboPython Multi-Module Compiler

Orchestrates compilation of multiple modules, handling:
- Module discovery and dependency resolution
- Circular import detection
- Compilation in dependency order
- Export tracking for cross-module references
"""

from __future__ import annotations
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .parse import Parser, ParseError, TpyModule, TpyImport, RelativeImportKey, SourceLocation
from .sema import SemanticAnalyzer, SemanticError, DiagnosticLevel
from .modules.resolver import ModuleResolver, ResolvedModule
from .modules import get_builtin_module_names
from .codegen_cpp import CodeGenerator, CodeGenOptions
from .codegen_cpp.context import module_to_cpp_namespace, set_namespace_map, clear_namespace_map
from .typesys import TpyType, INT32, INT64, BIGINT, clear_all_compilation_state

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


@dataclass
class CppCompilerConfig:
    """Configuration for the C++ compiler used to build generated code."""
    compiler: str = "g++"
    std: str = "c++23"
    extra_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    ccache: bool = False

    @classmethod
    def from_env(cls) -> CppCompilerConfig:
        """Create config from environment variables. Respects CXX for compiler selection."""
        compiler = os.environ.get("CXX", "g++")
        ccache = shutil.which("ccache") is not None
        return cls(compiler=compiler, ccache=ccache)


class BuildLayout:
    """Manages the build directory structure for compiled modules.

    Layout:
        output_dir/
          {entry_module}.d/        # root_dir
            include/               # headers (shared across variants)
              {module}.hpp
              {pkg}/{mod}.hpp
            src/                   # sources (shared across variants)
              {module}.cpp
              {pkg}/{mod}.cpp
            debug/                 # variant-specific build artifacts
              {entry_module}.o
              {entry_module}
            release/
              {entry_module}.o
              {entry_module}
    """

    def __init__(self, output_dir: Path, entry_module_name: str,
                 build_variant: str | None = None):
        self.output_dir = output_dir
        self.entry_module_name = entry_module_name
        self.root_dir = output_dir / f"{entry_module_name}.d"
        self.include_dir = self.root_dir / "include"
        self.src_dir = self.root_dir / "src"
        self.build_variant = build_variant
        self.build_dir = self.root_dir / build_variant if build_variant else self.root_dir

    def hpp_path(self, module_name: str) -> Path:
        """Path to the generated header for a module."""
        parts = module_name.split('.')
        if len(parts) == 1:
            return self.include_dir / f"{parts[0]}.hpp"
        return self.include_dir / Path(*parts[:-1]) / f"{parts[-1]}.hpp"

    def cpp_path(self, module_name: str) -> Path:
        """Path to the generated source for a module."""
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
        """Build the C++ compilation command(s).

        When ccache is enabled, splits into per-file compile steps (-c) plus a
        final link step so that ccache can cache each compilation unit.
        Otherwise returns a single compile-and-link command.

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
            config.compiler, f"-std={config.std}",
            *(opt_flags or []),
            *config.extra_flags,
            "-I", str(runtime_include_dir),
            "-I", str(self.include_dir),
            *extra_flags,
        ]

        if self.build_variant:
            self.build_dir.mkdir(parents=True, exist_ok=True)

        if not config.ccache:
            return [[
                *common,
                "-o", str(output),
                *[str(p) for p in cpp_files],
                *(extra_objects or []),
                *config.link_flags,
            ]]

        # Split: compile each .cpp -> .o with ccache, then link .o files
        obj_files: list[str] = []
        cmds: list[list[str]] = []
        for cpp in cpp_files:
            obj = self.build_dir / (cpp.stem + ".o")
            obj_files.append(str(obj))
            cmds.append([
                "ccache", *common,
                "-c", "-o", str(obj), str(cpp),
            ])
        cmds.append([
            config.compiler,
            "-o", str(output),
            *obj_files,
            *(extra_objects or []),
            *config.link_flags,
        ])
        return cmds


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
        self._source_input: tuple[str, str] | None = None

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
            parser = Parser()
            ast = parser.parse(source)
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
                self._compute_compile_order()
            else:
                self.compile_order = [entry_name]

            set_namespace_map(self._build_namespace_map())
            for name in self.compile_order:
                self._analyze_module(self.modules[name])
            return [self.modules[name] for name in self.compile_order]

        # 1. Discover all modules (starting from entry point)
        # Entry point uses simple name (not dotted) since it's the root
        entry_name = ModuleResolver.get_module_name(self.entry_point)
        self._discover_modules(entry_name, self.entry_point, [], is_entry_point=True)

        # 2. Compute compilation order (topological sort)
        self._compute_compile_order()

        # 3. Build namespace map from # tpy: namespace directives
        set_namespace_map(self._build_namespace_map())

        # 4. Parse and analyze in dependency order
        for module_name in self.compile_order:
            compiled = self.modules[module_name]
            self._analyze_module(compiled)

        # Return in dependency order
        return [self.modules[name] for name in self.compile_order]

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

        # Parse the module
        parser = Parser()
        try:
            source = path.read_text()
            ast = parser.parse(source)
        except ParseError as e:
            raise CompileError(e.message, module_name, path, lineno=e.lineno)

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

            self._process_user_import(imported_name, import_lineno, module_name, path, builtin_names, new_chain)

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

        return resolved_name

    def _process_user_import(
        self,
        imported_name: str,
        import_lineno: int,
        module_name: str,
        path: Path,
        builtin_names: set[str],
        new_chain: list[str],
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
                Path("<stdin>"), builtin_names, import_chain
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

        self.compile_order = result

    def _analyze_module(self, compiled: CompiledModule) -> None:
        """Analyze a single module.

        Args:
            compiled: The compiled module to analyze.
        """
        # Create analyzer
        analyzer = SemanticAnalyzer(default_int_type=self.default_int_type)

        # Register already-analyzed user modules in this analyzer's registry
        # This must happen before analyze() so _register_user_module_import can find them
        # Also register transitive dependencies so types referenced in method signatures
        # (e.g. iterator types) are visible even when not explicitly imported
        registered: set[str] = set()
        queue = list(compiled.ast.user_module_imports)
        while queue:
            dep_name = queue.pop()
            if dep_name in registered or dep_name not in self.modules:
                continue
            registered.add(dep_name)
            dep_compiled = self.modules[dep_name]
            module_info = self._exports_to_module_info(dep_name, dep_compiled.exports, dep_compiled)
            self._merge_builtin_supplements(module_info)
            analyzer.registry.register_module(module_info)
            # Enqueue transitive dependencies
            for transitive in self.modules[dep_name].ast.user_module_imports:
                if transitive not in registered:
                    queue.append(transitive)

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

        # For __init__.py, also re-export imported functions from user modules
        if compiled.is_package_init:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_functions.items():
                if local_name not in exports.functions:
                    # Get the function info from the source module
                    module_info = analyzer.registry.get_module(source_module)
                    if module_info and original_name in module_info.functions:
                        func_infos = module_info.functions[original_name]
                        if func_infos:
                            exports.functions[local_name] = func_infos
                            # Track re-export source for codegen
                            exports.reexported_functions[local_name] = (source_module, original_name)

        # Export all user-defined records
        for record in compiled.ast.records:
            record_info = analyzer.registry.get_record(record.name)
            if record_info:
                exports.records[record.name] = record_info

        # For __init__.py, also re-export imported records from user modules
        if compiled.is_package_init:
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

        # For __init__.py, also re-export imported protocols from user modules
        if compiled.is_package_init:
            for local_name, (source_module, original_name) in analyzer.ctx.user_imported_protocols.items():
                if local_name not in exports.protocols:
                    # Get the protocol info from the source module
                    module_info = analyzer.registry.get_module(source_module)
                    if module_info and original_name in module_info.protocols:
                        exports.protocols[local_name] = module_info.protocols[original_name]

        # Export all user-defined enums
        for enum in compiled.ast.enums:
            enum_type = analyzer.registry.get_enum(enum.name)
            if enum_type:
                exports.enums[enum.name] = enum_type

        # For __init__.py, also re-export imported enums from user modules
        if compiled.is_package_init:
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
        from .typesys import ModuleInfo, ModuleVarInfo

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
            variables[k] = ModuleVarInfo(k, v, cpp_expr)

        is_native = compiled.ast.directives.native_module if compiled else False
        return ModuleInfo(
            name=name,
            is_builtin=False,
            is_native_module=is_native,
            functions=functions,
            variables=variables,
            records=exports.records,
            protocols=exports.protocols,
            type_aliases=exports.type_aliases,
            enums=exports.enums,
        )

    def _merge_builtin_supplements(self, module_info: 'ModuleInfo') -> None:
        """Supplement a .py module's exports with definitions from a same-named builtin.

        Enables hybrid modules: .py defines most functions, builtin provides
        functions that need special compiler support (e.g., INT type params,
        custom validation). Only adds missing names -- .py definitions take precedence.
        """
        from .modules import get_module as get_builtin_module, builtin_function_to_info
        builtin = get_builtin_module(module_info.name)
        if builtin is None:
            return
        for func_name, fn_def in builtin.functions.items():
            if func_name not in module_info.functions:
                module_info.functions[func_name] = builtin_function_to_info(
                    fn_def, module_info.name)
            else:
                # Merge: add builtin overloads not covered by .py definitions
                existing = module_info.functions[func_name]
                module_info.functions[func_name] = existing + builtin_function_to_info(
                    fn_def, module_info.name)

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
                      options: CodeGenOptions | None = None) -> tuple[Path, Path | None]:
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

        layout = BuildLayout(output_dir, entry_module_name)
        hpp_path = layout.hpp_path(mod_name)
        cpp_path = layout.cpp_path(mod_name)

        hpp_path.parent.mkdir(parents=True, exist_ok=True)
        cpp_path.parent.mkdir(parents=True, exist_ok=True)

        codegen = CodeGenerator(compiled.analyzer, options)
        # Pass actual user modules (those in self.modules, not builtins without user files)
        actual_user_modules = set(self.modules.keys())
        hpp_code, cpp_code = codegen.generate(
            compiled.ast, mod_name,
            is_entry_point=compiled.is_entry_point,
            actual_user_modules=actual_user_modules,
            reexported_functions=compiled.exports.reexported_functions,
            reexported_records=compiled.exports.reexported_records,
            reexported_variables=compiled.exports.reexported_variables,
            reexported_enums=compiled.exports.reexported_enums
        )

        hpp_path.write_text(hpp_code)
        if compiled.ast.directives.native_module:
            return hpp_path, None
        cpp_path.write_text(cpp_code)

        return hpp_path, cpp_path

    def generate_code_to_strings(self, compiled: CompiledModule,
                                  options: CodeGenOptions | None = None) -> tuple[str, str]:
        """Generate C++ code and return as strings (no file I/O)."""
        self._check_no_errors(compiled)
        codegen = CodeGenerator(compiled.analyzer, options)
        actual_user_modules = set(self.modules.keys())
        return codegen.generate(
            compiled.ast, compiled.name,
            is_entry_point=compiled.is_entry_point,
            actual_user_modules=actual_user_modules,
            reexported_functions=compiled.exports.reexported_functions,
            reexported_records=compiled.exports.reexported_records,
            reexported_variables=compiled.exports.reexported_variables,
            reexported_enums=compiled.exports.reexported_enums
        )

    def _build_namespace_map(self) -> dict[str, str]:
        """Build module_name -> C++ namespace mapping from # tpy: namespace directives.

        Rules:
        - Direct directive: module uses the specified namespace as-is.
        - Package inheritance: if __init__.py has a directive, child modules
          use "parent_ns::relative_child" (most-specific parent wins).
        - No directive: default "tpy_user::module_name".
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

            ns_map[name] = resolved or f"tpy_user::{name.replace('.', '::')}"

        return ns_map

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
