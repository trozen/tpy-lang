"""
TurboPython C++ Code Generator

Main orchestrator for generating C++ code from TurboPython AST.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TextIO, TYPE_CHECKING
import io

from ..typesys import TpyType, NamedType
from ..parse import TpyModule, TpyRecord, TpyVarDecl

from .context import CodeGenContext, CodeGenOptions, module_to_cpp_namespace
from .types import TypeResolver
from .protocols import ProtocolGenerator
from .builtins import BuiltinGenerator
from .expressions import ExpressionGenerator
from .statements import StatementGenerator
from .records import RecordGenerator
from .functions import FunctionGenerator

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer


@dataclass
class _ProtocolDeps:
    """Dependency sets computed for protocol ordering."""
    protocol_referenced_records: set[str] = field(default_factory=set)
    bound_protocols: set[str] = field(default_factory=set)
    bound_protocol_records: set[str] = field(default_factory=set)
    early_definition_records: set[str] = field(default_factory=set)
    prereq_protocols: set[str] = field(default_factory=set)
    prereq_protocol_records: set[str] = field(default_factory=set)
    module_record_names: set[str] = field(default_factory=set)
    records_by_name: dict[str, TpyRecord] = field(default_factory=dict)


class CodeGenerator:
    """Generates C++ code from TurboPython AST."""

    def __init__(self, analyzer: SemanticAnalyzer, options: CodeGenOptions | None = None):
        self.analyzer = analyzer
        self.options = options or CodeGenOptions()

        # Create context (shared state)
        self.ctx = CodeGenContext(
            analyzer=analyzer,
            options=self.options,
        )

        # Create component generators
        self.types = TypeResolver(self.ctx)
        self.protocols = ProtocolGenerator(self.ctx)
        self.builtins = BuiltinGenerator(self.ctx, self.types)
        self.expressions = ExpressionGenerator(self.ctx, self.types, self.builtins, self.protocols)
        self.statements = StatementGenerator(self.ctx, self.types, self.builtins)
        self.records = RecordGenerator(self.ctx, self.types, self.protocols)
        self.functions = FunctionGenerator(self.ctx, self.types, self.protocols)

        # Wire up circular dependencies
        self.statements.set_expressions(self.expressions)
        self.records.set_dependencies(self.expressions, self.statements)
        self.functions.set_statements(self.statements)

    def generate(self, module: TpyModule, module_name: str = "generated",
                 is_entry_point: bool = True,
                 actual_user_modules: set[str] | None = None,
                 reexported_functions: dict[str, tuple[str, str]] | None = None,
                 reexported_records: dict[str, tuple[str, str]] | None = None,
                 reexported_variables: dict[str, tuple[str, str]] | None = None) -> tuple[str, str]:
        """Generate C++ header and source files.

        Args:
            module: The parsed TurboPython module AST.
            module_name: Name for the generated files (used in #include).
            is_entry_point: True if this is the entry point module (generates main()).
            actual_user_modules: Set of module names that are actually user modules (have .tp.py files).
                                 If None, uses module.user_module_imports (legacy behavior).
            reexported_functions: Dict of {local_name: (source_module, original_name)} for re-exports.
            reexported_records: Dict of {local_name: (source_module, original_name)} for re-exports.
            reexported_variables: Dict of {local_name: (source_module, original_name)} for re-exports.
        """
        self.ctx.module_name = module_name
        self.ctx.source_lines = module.source_lines
        # Filter user_module_imports to only include actual user modules (not builtins without user files)
        if actual_user_modules is not None:
            self.ctx.user_module_imports = {k: v for k, v in module.user_module_imports.items() if k in actual_user_modules}
            self.ctx.all_user_modules = actual_user_modules
        else:
            self.ctx.user_module_imports = module.user_module_imports
            self.ctx.all_user_modules = set(module.user_module_imports.keys())
        self.ctx.user_imported_functions = dict(self.analyzer.ctx.user_imported_functions)
        self.ctx.user_imported_records = dict(self.analyzer.ctx.user_imported_records)
        self.ctx.user_imported_protocols = dict(self.analyzer.ctx.user_imported_protocols)
        self.ctx.user_imported_variables = dict(self.analyzer.ctx.user_imported_variables)
        self.ctx.top_level_decls = dict(self.analyzer.ctx.top_level_decls)
        self.ctx.reexported_functions = reexported_functions or {}
        self.ctx.reexported_records = reexported_records or {}
        self.ctx.reexported_variables = reexported_variables or {}
        hpp = io.StringIO()
        cpp = io.StringIO()

        self._write_header_preamble(hpp)
        self._write_source_preamble(cpp)

        # Separate global declarations from other top-level statements early
        # (needed for extern declarations in header)
        # Track seen names and types to handle re-declarations (z = 0; z = 5; → one global, one assignment)
        global_decls = []
        seen_globals: dict[str, TpyType | None] = {}
        for stmt in module.top_level_stmts:
            if isinstance(stmt, TpyVarDecl):
                if stmt.name not in seen_globals:
                    global_decls.append(stmt)
                    # Store the type for this global
                    var_type = stmt.type
                    if var_type is None and stmt.init:
                        var_type = self.types.get_resolved_type(stmt.init)
                    seen_globals[stmt.name] = var_type

        # Store global names for use in expression generation (method/field access)
        self.ctx.global_names = set(seen_globals.keys())
        # Track if we need synthetic __name__ (add to global_names for proper deref)
        self.ctx._has_synthetic_name = "__name__" not in seen_globals
        if self.ctx._has_synthetic_name:
            self.ctx.global_names.add("__name__")

        # Generate protocol ordering and forward declarations
        self._generate_protocol_ordering(hpp, module, global_decls, seen_globals)

        # Generate global definitions in source (before functions)
        # __name__ is always present (synthetic if not user-defined)
        if "__name__" not in seen_globals:
            cpp.write('tpy::Global<std::string_view> __name__;\n')
        for stmt in global_decls:
            self.functions.gen_global_decl(cpp, stmt)
        cpp.write("\n")

        # Generate function definitions
        for func in module.functions:
            self.functions.gen_function_def(cpp, func)
            cpp.write("\n")

        # Generate module init function and main()
        # Always generate __tpy_init for global initialization (Python semantics)
        # Pass ALL top-level statements to init function (including globals)
        # Note: has_user_main=False because users should call main() explicitly at top level,
        # either as `main()` or `if __name__ == "__main__": main()`
        self.functions.gen_module_init_decl(hpp)
        # Pass module name for __name__ variable initialization
        tpy_module_name = self.analyzer.ctx.module_name
        # Imports are now included in top_level_stmts as TpyImport nodes
        # They get emitted as __tpy_init() calls in statement order (Python semantics)
        self.functions.gen_module_init(cpp, module.top_level_stmts, seen_globals,
                                       has_user_main=False, module_name=tpy_module_name)
        # Only generate C++ main() for entry point module
        if is_entry_point:
            self.functions.gen_main(cpp)
        else:
            # For non-entry-point modules, just close the namespace
            self.functions.gen_namespace_close(cpp)

        self._write_header_epilogue(hpp)

        return hpp.getvalue(), cpp.getvalue()

    def _generate_protocol_ordering(self, hpp: TextIO, module: TpyModule,
                                    global_decls: list, seen_globals: dict) -> None:
        """Generate protocols, forward declarations, and records in proper order.

        C++ requires forward declarations and concepts to be defined before use.
        This method handles the complex ordering requirements.
        """
        deps = self._collect_protocol_deps(module)
        self._generate_forward_decls_and_concepts(hpp, module, deps)
        self._generate_definitions_and_reexports(hpp, module, global_decls, seen_globals, deps)

    def _collect_protocol_deps(self, module: TpyModule) -> _ProtocolDeps:
        """Collect all dependency sets needed for protocol ordering."""
        deps = _ProtocolDeps(
            module_record_names={r.name for r in module.records},
            records_by_name={r.name: r for r in module.records},
        )

        # Records referenced in protocol signatures
        for protocol in module.protocols:
            for method_sig in protocol.methods:
                self.protocols.collect_record_types_from_type(method_sig.return_type, deps.protocol_referenced_records)
                for _, param_type in method_sig.params:
                    self.protocols.collect_record_types_from_type(param_type, deps.protocol_referenced_records)
            for _, field_type in protocol.fields:
                self.protocols.collect_record_types_from_type(field_type, deps.protocol_referenced_records)

        # User-defined protocols used as bounds on protocol-referenced records
        for record_name in deps.protocol_referenced_records:
            if record_name in deps.module_record_names:
                record = deps.records_by_name[record_name]
                for bound in record.type_param_bounds.values():
                    proto_info = self.analyzer.registry.get_protocol(bound.name)
                    if proto_info is None or proto_info.cpp_concept is None:
                        deps.bound_protocols.add(bound.name)

        # Records referenced by bound protocols
        for protocol in module.protocols:
            if protocol.name in deps.bound_protocols:
                for method_sig in protocol.methods:
                    self.protocols.collect_record_types_from_type(method_sig.return_type, deps.bound_protocol_records)
                    for _, param_type in method_sig.params:
                        self.protocols.collect_record_types_from_type(param_type, deps.bound_protocol_records)
                for _, field_type in protocol.fields:
                    self.protocols.collect_record_types_from_type(field_type, deps.bound_protocol_records)

        # Records used as type args to bounded records in protocol signatures
        for protocol in module.protocols:
            if protocol.name in deps.bound_protocols:
                continue
            for method_sig in protocol.methods:
                self.protocols.collect_type_args_of_bounded_records(
                    method_sig.return_type, deps.records_by_name, deps.early_definition_records
                )
                for _, param_type in method_sig.params:
                    self.protocols.collect_type_args_of_bounded_records(
                        param_type, deps.records_by_name, deps.early_definition_records
                    )
            for _, field_type in protocol.fields:
                self.protocols.collect_type_args_of_bounded_records(
                    field_type, deps.records_by_name, deps.early_definition_records
                )

        # Prereq protocols: user-defined protocols that are bounds on bound_protocol_records
        for record_name in deps.bound_protocol_records:
            if record_name in deps.module_record_names:
                record = deps.records_by_name[record_name]
                for bound in record.type_param_bounds.values():
                    proto_info = self.analyzer.registry.get_protocol(bound.name)
                    if proto_info is None or proto_info.cpp_concept is None:
                        deps.prereq_protocols.add(bound.name)

        # Records referenced by prereq protocols
        for protocol in module.protocols:
            if protocol.name in deps.prereq_protocols:
                for method_sig in protocol.methods:
                    self.protocols.collect_record_types_from_type(method_sig.return_type, deps.prereq_protocol_records)
                    for _, param_type in method_sig.params:
                        self.protocols.collect_record_types_from_type(param_type, deps.prereq_protocol_records)
                for _, field_type in protocol.fields:
                    self.protocols.collect_record_types_from_type(field_type, deps.prereq_protocol_records)

        return deps

    def _generate_forward_decls_and_concepts(
        self, hpp: TextIO, module: TpyModule, deps: _ProtocolDeps
    ) -> None:
        """Generate forward declarations for prereq/bound/protocol-referenced records and concepts."""
        # Forward declare records referenced by prereq protocols (unconstrained)
        for record_name in sorted(deps.prereq_protocol_records):
            if record_name in deps.module_record_names:
                record = deps.records_by_name[record_name]
                if record.type_params:
                    tparams = ", ".join(f"typename {tp}" for tp in record.type_params)
                    hpp.write(f"template<{tparams}> struct {record_name};\n")
                else:
                    hpp.write(f"struct {record_name};\n")

        if deps.prereq_protocol_records & deps.module_record_names:
            hpp.write("\n")

        # Prereq protocol concepts
        for protocol in module.protocols:
            if protocol.name in deps.prereq_protocols:
                self.protocols.gen_concept_decl(hpp, protocol)
                hpp.write("\n")

        # Forward declare records referenced by bound protocols
        for record_name in sorted(deps.bound_protocol_records):
            if record_name in deps.module_record_names:
                if record_name in deps.prereq_protocol_records:
                    continue
                record = deps.records_by_name[record_name]
                if record.type_params:
                    if record.type_param_bounds or record.type_param_kinds:
                        template_header = self.protocols.gen_record_template_header(
                            record.type_params, record.type_param_bounds, record.type_param_kinds
                        )
                        hpp.write(f"{template_header} struct {record_name};\n")
                    else:
                        tparams = ", ".join(f"typename {tp}" for tp in record.type_params)
                        hpp.write(f"template<{tparams}> struct {record_name};\n")
                else:
                    hpp.write(f"struct {record_name};\n")

        if deps.bound_protocol_records & deps.module_record_names:
            hpp.write("\n")

        # Bound protocol concepts (skip prereq protocols already emitted)
        for protocol in module.protocols:
            if protocol.name in deps.bound_protocols and protocol.name not in deps.prereq_protocols:
                self.protocols.gen_concept_decl(hpp, protocol)
                hpp.write("\n")

        # Fully define records referenced by bound protocols
        for record in module.records:
            if record.name in deps.bound_protocol_records:
                self.records.gen_record_decl(hpp, record)
                hpp.write("\n")

        # Full definitions for records that are type args to bounded records
        for record in module.records:
            if record.name in deps.early_definition_records:
                if record.name in deps.bound_protocol_records:
                    continue
                self.records.gen_record_decl(hpp, record)
                hpp.write("\n")

        # Forward declare records referenced in protocols (bounds now available)
        for record_name in sorted(deps.protocol_referenced_records):
            if record_name in deps.module_record_names:
                if record_name in deps.early_definition_records:
                    continue
                if record_name in deps.bound_protocol_records:
                    continue
                if record_name in deps.prereq_protocol_records:
                    continue
                record = deps.records_by_name[record_name]
                if record.type_params:
                    template_header = self.protocols.gen_record_template_header(
                        record.type_params, record.type_param_bounds, record.type_param_kinds
                    )
                    hpp.write(f"{template_header} struct {record_name};\n")
                else:
                    hpp.write(f"struct {record_name};\n")

        if deps.protocol_referenced_records & deps.module_record_names:
            hpp.write("\n")

        # Remaining C++20 concepts for user-defined protocols
        for protocol in module.protocols:
            if protocol.name not in deps.bound_protocols and protocol.name not in deps.prereq_protocols:
                self.protocols.gen_concept_decl(hpp, protocol)
                hpp.write("\n")

    def _generate_definitions_and_reexports(
        self, hpp: TextIO, module: TpyModule,
        global_decls: list, seen_globals: dict, deps: _ProtocolDeps
    ) -> None:
        """Generate remaining forward decls, global externs, record definitions, functions, and re-exports."""
        # Forward declare remaining records
        for record in module.records:
            if record.name in deps.protocol_referenced_records:
                continue
            if record.name in deps.early_definition_records:
                continue
            if record.name in deps.bound_protocol_records:
                continue
            if record.name in deps.prereq_protocol_records:
                continue
            if record.type_params:
                template_header = self.protocols.gen_record_template_header(
                    record.type_params, record.type_param_bounds, record.type_param_kinds
                )
                hpp.write(f"{template_header} struct {record.name};\n")
            else:
                hpp.write(f"struct {record.name};\n")
        if module.records:
            hpp.write("\n")

        # Global extern declarations
        if "__name__" not in seen_globals:
            hpp.write("extern tpy::Global<std::string_view> __name__;\n")
        for stmt in global_decls:
            self.functions.gen_global_extern(hpp, stmt)
        hpp.write("\n")

        # Full record definitions (skip those already defined early)
        sorted_records = self.records.sort_records_by_inheritance(module.records)
        for record in sorted_records:
            if record.name in deps.early_definition_records:
                continue
            if record.name in deps.bound_protocol_records:
                continue
            self.records.gen_record_decl(hpp, record)
            hpp.write("\n")

        # Function declarations
        for func in module.functions:
            self.functions.gen_function_decl(hpp, func)
        hpp.write("\n")

        # Re-exported functions
        if self.ctx.reexported_functions:
            for local_name, (source_module, original_name) in sorted(self.ctx.reexported_functions.items()):
                qualified = f"{module_to_cpp_namespace(source_module)}::{original_name}"
                if local_name == original_name:
                    hpp.write(f"using {qualified};\n")
                else:
                    hpp.write(f"inline auto& {local_name} = {qualified};\n")
            hpp.write("\n")

        # Re-exported records
        if self.ctx.reexported_records:
            for local_name, (source_module, original_name) in sorted(self.ctx.reexported_records.items()):
                qualified = f"{module_to_cpp_namespace(source_module)}::{original_name}"
                if local_name == original_name:
                    hpp.write(f"using {qualified};\n")
                else:
                    hpp.write(f"using {local_name} = {qualified};\n")
            hpp.write("\n")

        # Re-exported variables
        if self.ctx.reexported_variables:
            for local_name, (source_module, original_name) in sorted(self.ctx.reexported_variables.items()):
                qualified = f"{module_to_cpp_namespace(source_module)}::{original_name}"
                hpp.write(f"inline auto& {local_name} = {qualified};\n")
            hpp.write("\n")


    def _module_to_include_path(self, module_name: str) -> str:
        """Convert dotted module name to include path.

        Args:
            module_name: Dotted module name (e.g., "mypackage.submod").

        Returns:
            Include path (e.g., "mypackage/submod.hpp").
        """
        parts = module_name.split('.')
        if len(parts) == 1:
            return f"{parts[0]}.hpp"
        # pkg.submod -> pkg/submod.hpp
        return '/'.join(parts[:-1]) + f"/{parts[-1]}.hpp"

    def _write_header_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write("#pragma once\n\n")
        out.write('#include <tpy/tpy.hpp>\n')
        # Include user module headers (including parent packages for dotted imports)
        # Skip builtin modules - they don't have separate header files
        included = set()
        for user_mod in sorted(self.ctx.user_module_imports):
            # Skip builtin modules (math, time, sys) - they're part of the runtime
            module_info = self.analyzer.registry.get_module(user_mod)
            if module_info and module_info.is_builtin:
                continue
            # For dotted imports, include parent package headers first
            parts = user_mod.split('.')
            for i in range(1, len(parts)):
                parent_pkg = '.'.join(parts[:i])
                if parent_pkg in self.ctx.all_user_modules and parent_pkg not in included:
                    include_path = self._module_to_include_path(parent_pkg)
                    out.write(f'#include "{include_path}"\n')
                    included.add(parent_pkg)
            # Then include the module itself
            if user_mod not in included:
                include_path = self._module_to_include_path(user_mod)
                out.write(f'#include "{include_path}"\n')
                included.add(user_mod)
        out.write("\n")
        # Use nested namespace for dotted module names
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"namespace {ns} {{\n\n")

    def _write_source_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        # Use full include path from include root (consistent with header includes)
        include_path = self._module_to_include_path(self.ctx.module_name)
        out.write(f'#include "{include_path}"\n\n')
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"namespace {ns} {{\n\n")

    def _write_header_epilogue(self, out: TextIO) -> None:
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n")
