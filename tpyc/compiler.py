"""
TurboPython Multi-Module Compiler

Orchestrates compilation of multiple modules, handling:
- Module discovery and dependency resolution
- Circular import detection
- Compilation in dependency order
- Export tracking for cross-module references
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .parse import Parser, ParseError, TpyModule
from .sema import SemanticAnalyzer, SemanticError
from .modules.resolver import ModuleResolver
from .modules import get_builtin_module_names
from .codegen_cpp import CodeGenerator, CodeGenOptions

if TYPE_CHECKING:
    from .typesys import TpyType, FunctionInfo, RecordInfo, ProtocolInfo, ModuleInfo, ModuleVarInfo


@dataclass
class ModuleExports:
    """Exports from a compiled module.

    Contains all items that can be imported from this module:
    - functions: dict of function name -> FunctionInfo
    - records: dict of record name -> RecordInfo
    - protocols: dict of protocol name -> ProtocolInfo
    - variables: dict of variable name -> TpyType
    """
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    records: dict[str, RecordInfo] = field(default_factory=dict)
    protocols: dict[str, ProtocolInfo] = field(default_factory=dict)
    variables: dict[str, TpyType] = field(default_factory=dict)


@dataclass
class CompiledModule:
    """A compiled module with its AST and exports."""
    name: str
    path: Path
    ast: TpyModule
    exports: ModuleExports
    is_entry_point: bool
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

    def __init__(self, entry_point: Path):
        """Initialize compiler with entry point path.

        Args:
            entry_point: Path to the main .tp.py file.
        """
        self.entry_point = entry_point.resolve()
        self.resolver = ModuleResolver(self.entry_point.parent)
        self.modules: dict[str, CompiledModule] = {}
        self.compile_order: list[str] = []
        # Track which modules shadow builtins: {module_name: set of (importing_module, line_no)}
        self.shadowed_builtins: dict[str, set[tuple[str, int]]] = {}

    def compile(self) -> list[CompiledModule]:
        """Compile entry point and all imported modules.

        Returns:
            List of CompiledModule in dependency order (dependencies first).

        Raises:
            CompileError: If circular imports detected or module not found.
            ParseError: If parsing fails.
            SemanticError: If semantic analysis fails.
        """
        # 1. Discover all modules (starting from entry point)
        entry_name = ModuleResolver.get_module_name(self.entry_point)
        self._discover_modules(entry_name, self.entry_point, [])

        # 2. Compute compilation order (topological sort)
        self._compute_compile_order()

        # 3. Parse and analyze in dependency order
        for module_name in self.compile_order:
            compiled = self.modules[module_name]
            self._analyze_module(compiled)

        # Return in dependency order
        return [self.modules[name] for name in self.compile_order]

    def _discover_modules(self, module_name: str, path: Path, import_chain: list[str],
                          import_lineno: int | None = None) -> None:
        """Recursively discover modules starting from the given module.

        Args:
            module_name: Name of the module to discover.
            path: Path to the module file.
            import_chain: Current import chain for cycle detection.
            import_lineno: Line number of the import statement that triggered this discovery.

        Raises:
            CompileError: If circular import detected or module not found.
        """
        # Check for circular imports
        if module_name in import_chain:
            cycle = " -> ".join(import_chain + [module_name])
            # Report error on the importing module (last in chain) at the import line
            importing_module = import_chain[-1] if import_chain else module_name
            importing_path = self.modules[importing_module].path if importing_module in self.modules else path
            raise CompileError(f"Circular import detected: {cycle}", importing_module, importing_path,
                               lineno=import_lineno)

        # Already discovered
        if module_name in self.modules:
            return

        # Parse the module
        parser = Parser()
        try:
            source = path.read_text()
            ast = parser.parse(source)
        except ParseError as e:
            raise CompileError(e.message, module_name, path, lineno=e.lineno)

        # Determine if this is the entry point
        is_entry = (path == self.entry_point)

        # Create module entry (exports filled during analysis)
        self.modules[module_name] = CompiledModule(
            name=module_name,
            path=path,
            ast=ast,
            exports=ModuleExports(),
            is_entry_point=is_entry,
        )

        # Recursively discover imported user modules
        new_chain = import_chain + [module_name]
        builtin_names = get_builtin_module_names()
        for imported_name, import_lineno in ast.user_module_imports.items():
            imported_path = self.resolver.resolve(imported_name)
            if imported_path is None:
                # No user file found - check if it's a builtin module
                if imported_name in builtin_names:
                    # Builtin module, no user file - skip (handled by builtin system)
                    continue
                raise CompileError(
                    f"Module '{imported_name}' not found",
                    module_name, path, lineno=import_lineno
                )
            # User file found - check if it shadows a builtin
            if imported_name in builtin_names:
                # Track for warning emission during analysis
                if imported_name not in self.shadowed_builtins:
                    self.shadowed_builtins[imported_name] = set()
                self.shadowed_builtins[imported_name].add((module_name, import_lineno))
            self._discover_modules(imported_name, imported_path, new_chain, import_lineno)

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
        analyzer = SemanticAnalyzer()

        # Register already-analyzed user modules in this analyzer's registry
        # This must happen before analyze() so _register_user_module_import can find them
        for dep_name in compiled.ast.user_module_imports:
            if dep_name in self.modules:
                dep_exports = self.modules[dep_name].exports
                module_info = self._exports_to_module_info(dep_name, dep_exports)
                analyzer.registry.register_module(module_info)

        # Set module name for __name__
        module_name = "__main__" if compiled.is_entry_point else compiled.name

        # Analyze the module
        analyzer.analyze(
            compiled.ast,
            module_name=module_name,
        )

        # Emit warnings for shadowed builtins imported by this module
        for shadowed_name, importers in self.shadowed_builtins.items():
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
        for func in compiled.ast.functions:
            func_info = analyzer.registry.get_function(func.name)
            if func_info:
                exports.functions[func.name] = func_info

        # Export all user-defined records
        for record in compiled.ast.records:
            record_info = analyzer.registry.get_record(record.name)
            if record_info:
                exports.records[record.name] = record_info

        # Export all user-defined protocols
        for protocol in compiled.ast.protocols:
            protocol_info = analyzer.registry.get_protocol(protocol.name)
            if protocol_info:
                exports.protocols[protocol.name] = protocol_info

        # Export global variables (from top-level statements)
        # These are tracked in the global scope, but we must exclude imported variables
        # UNLESS they were redefined at the top level (in top_level_decls)
        imported_var_names = set(analyzer.ctx.user_imported_variables.keys())
        top_level_decls = analyzer.ctx.top_level_decls
        for name, var_type in analyzer.global_scope.all().items():
            if name == "__name__":  # Don't export synthetic __name__
                continue
            # Don't re-export imported variables unless redefined at top level
            if name in imported_var_names and name not in top_level_decls:
                continue
            exports.variables[name] = var_type

    def _exports_to_module_info(self, name: str, exports: ModuleExports) -> 'ModuleInfo':
        """Convert ModuleExports to ModuleInfo for unified registry storage.

        Args:
            name: Module name.
            exports: The module exports to convert.

        Returns:
            ModuleInfo suitable for registry storage.
        """
        from .typesys import ModuleInfo, ModuleVarInfo

        # Wrap single functions in lists for uniform overload handling
        functions = {k: [v] for k, v in exports.functions.items()}

        # Create ModuleVarInfo with generated cpp_expr
        variables = {
            k: ModuleVarInfo(k, v, f"tpy_user::{name}::{k}")
            for k, v in exports.variables.items()
        }

        return ModuleInfo(
            name=name,
            is_builtin=False,
            functions=functions,
            variables=variables,
            records=exports.records,
            protocols=exports.protocols,
        )

    def generate_code(self, compiled: CompiledModule, output_dir: Path,
                      options: CodeGenOptions | None = None) -> tuple[Path, Path]:
        """Generate C++ code for a compiled module.

        Args:
            compiled: The compiled module.
            output_dir: Root output directory.
            options: Code generation options (optional).

        Returns:
            Tuple of (hpp_path, cpp_path) for the generated files.
        """
        mod_name = compiled.name
        mod_dir = output_dir / f"{mod_name}.d"
        mod_dir.mkdir(parents=True, exist_ok=True)

        codegen = CodeGenerator(compiled.analyzer, options)
        # Pass actual user modules (those in self.modules, not builtins without user files)
        actual_user_modules = set(self.modules.keys())
        hpp_code, cpp_code = codegen.generate(
            compiled.ast, mod_name,
            is_entry_point=compiled.is_entry_point,
            actual_user_modules=actual_user_modules
        )

        hpp_path = mod_dir / f"{mod_name}.hpp"
        cpp_path = mod_dir / f"{mod_name}.cpp"
        hpp_path.write_text(hpp_code)
        cpp_path.write_text(cpp_code)

        return hpp_path, cpp_path
