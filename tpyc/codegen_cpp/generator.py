"""
TurboPython C++ Code Generator

Main orchestrator for generating C++ code from TurboPython AST.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING
import io

from ..typesys import TpyType, RecordType
from ..parse import TpyModule, TpyVarDecl

from .context import CodeGenContext, CodeGenOptions
from .types import TypeResolver
from .protocols import ProtocolGenerator
from .builtins import BuiltinGenerator
from .expressions import ExpressionGenerator
from .statements import StatementGenerator
from .records import RecordGenerator
from .functions import FunctionGenerator

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer


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
                 all_imported_modules: list[str] | None = None,
                 actual_user_modules: set[str] | None = None) -> tuple[str, str]:
        """Generate C++ header and source files.

        Args:
            module: The parsed TurboPython module AST.
            module_name: Name for the generated files (used in #include).
            is_entry_point: True if this is the entry point module (generates main()).
            all_imported_modules: For entry point, list of ALL modules to initialize (in order).
            actual_user_modules: Set of module names that are actually user modules (have .tp.py files).
                                 If None, uses module.user_module_imports (legacy behavior).
        """
        self.ctx.module_name = module_name
        self.ctx.source_lines = module.source_lines
        # Filter user_module_imports to only include actual user modules (not builtins without user files)
        if actual_user_modules is not None:
            self.ctx.user_module_imports = {k: v for k, v in module.user_module_imports.items() if k in actual_user_modules}
        else:
            self.ctx.user_module_imports = module.user_module_imports
        self.ctx.user_imported_functions = dict(self.analyzer.ctx.user_imported_functions)
        self.ctx.user_imported_records = dict(self.analyzer.ctx.user_imported_records)
        self.ctx.user_imported_protocols = dict(self.analyzer.ctx.user_imported_protocols)
        self.ctx.user_imported_variables = dict(self.analyzer.ctx.user_imported_variables)
        self.ctx.top_level_decls = dict(self.analyzer.ctx.top_level_decls)
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
        # Only entry point module initializes imported modules (to avoid duplicate init)
        # Use all_imported_modules if provided (in dependency order), otherwise fall back to direct imports
        if is_entry_point:
            imported_modules = all_imported_modules if all_imported_modules is not None else sorted(self.ctx.user_module_imports)
        else:
            imported_modules = None
        self.functions.gen_module_init(cpp, module.top_level_stmts, seen_globals,
                                       has_user_main=False, module_name=tpy_module_name,
                                       imported_modules=imported_modules)
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
        # Collect all record types referenced in protocol signatures
        # These need to be forward declared BEFORE concepts are generated
        protocol_referenced_records: set[str] = set()
        for protocol in module.protocols:
            for method_sig in protocol.methods:
                self.protocols.collect_record_types_from_type(method_sig.return_type, protocol_referenced_records)
                for _, param_type in method_sig.params:
                    self.protocols.collect_record_types_from_type(param_type, protocol_referenced_records)
            for _, field_type in protocol.fields:
                self.protocols.collect_record_types_from_type(field_type, protocol_referenced_records)

        module_record_names = {r.name for r in module.records}
        records_by_name = {r.name: r for r in module.records}

        # Find user-defined protocols that are used as bounds on protocol-referenced records
        # These concepts must be generated BEFORE forward declaring those records
        bound_protocols: set[str] = set()
        for record_name in protocol_referenced_records:
            if record_name in module_record_names:
                record = records_by_name[record_name]
                for bound in record.type_param_bounds.values():
                    proto_info = self.analyzer.registry.get_protocol(bound.name)
                    if proto_info is None or proto_info.cpp_concept is None:
                        # User-defined protocol used as bound
                        bound_protocols.add(bound.name)

        # Collect records referenced by bound protocols - these need forward decl first
        bound_protocol_records: set[str] = set()
        for protocol in module.protocols:
            if protocol.name in bound_protocols:
                for method_sig in protocol.methods:
                    self.protocols.collect_record_types_from_type(method_sig.return_type, bound_protocol_records)
                    for _, param_type in method_sig.params:
                        self.protocols.collect_record_types_from_type(param_type, bound_protocol_records)
                for _, field_type in protocol.fields:
                    self.protocols.collect_record_types_from_type(field_type, bound_protocol_records)

        # Find records used as type arguments to bounded records in protocol signatures
        # These need FULL DEFINITIONS early so C++ can verify constraint satisfaction
        early_definition_records: set[str] = set()
        for protocol in module.protocols:
            if protocol.name in bound_protocols:
                continue  # Skip bound protocols - they don't reference other records
            for method_sig in protocol.methods:
                self.protocols.collect_type_args_of_bounded_records(
                    method_sig.return_type, records_by_name, early_definition_records
                )
                for _, param_type in method_sig.params:
                    self.protocols.collect_type_args_of_bounded_records(
                        param_type, records_by_name, early_definition_records
                    )
            for _, field_type in protocol.fields:
                self.protocols.collect_type_args_of_bounded_records(
                    field_type, records_by_name, early_definition_records
                )

        # Find user-defined protocols that are bounds on bound_protocol_records
        # These "prereq protocols" must be emitted before we can forward declare those records
        prereq_protocols: set[str] = set()
        for record_name in bound_protocol_records:
            if record_name in module_record_names:
                record = records_by_name[record_name]
                for bound in record.type_param_bounds.values():
                    proto_info = self.analyzer.registry.get_protocol(bound.name)
                    if proto_info is None or proto_info.cpp_concept is None:
                        prereq_protocols.add(bound.name)

        # Collect records referenced by prereq protocols - need forward decl first
        prereq_protocol_records: set[str] = set()
        for protocol in module.protocols:
            if protocol.name in prereq_protocols:
                for method_sig in protocol.methods:
                    self.protocols.collect_record_types_from_type(method_sig.return_type, prereq_protocol_records)
                    for _, param_type in method_sig.params:
                        self.protocols.collect_record_types_from_type(param_type, prereq_protocol_records)
                for _, field_type in protocol.fields:
                    self.protocols.collect_record_types_from_type(field_type, prereq_protocol_records)

        # Forward declare records referenced by prereq protocols
        for record_name in sorted(prereq_protocol_records):
            if record_name in module_record_names:
                record = records_by_name[record_name]
                if record.type_params:
                    # Use unconstrained forward decl - bounds may not be available yet
                    tparams = ", ".join(f"typename {tp}" for tp in record.type_params)
                    hpp.write(f"template<{tparams}> struct {record_name};\n")
                else:
                    hpp.write(f"struct {record_name};\n")

        if prereq_protocol_records & module_record_names:
            hpp.write("\n")

        # Generate prereq protocols (bounds needed for constrained forward decls)
        for protocol in module.protocols:
            if protocol.name in prereq_protocols:
                self.protocols.gen_concept_decl(hpp, protocol)
                hpp.write("\n")

        # Forward declare records referenced by bound protocols
        for record_name in sorted(bound_protocol_records):
            if record_name in module_record_names:
                if record_name in prereq_protocol_records:
                    continue  # Already forward declared above
                record = records_by_name[record_name]
                if record.type_params:
                    if record.type_param_bounds or record.type_param_kinds:
                        # Use constrained forward decl - bounds or int params are present
                        template_header = self.protocols.gen_record_template_header(
                            record.type_params, record.type_param_bounds, record.type_param_kinds
                        )
                        hpp.write(f"{template_header} struct {record_name};\n")
                    else:
                        # No bounds or int params - unconstrained forward decl
                        tparams = ", ".join(f"typename {tp}" for tp in record.type_params)
                        hpp.write(f"template<{tparams}> struct {record_name};\n")
                else:
                    hpp.write(f"struct {record_name};\n")

        if bound_protocol_records & module_record_names:
            hpp.write("\n")

        # Generate bound protocol concepts (skip prereq protocols already emitted)
        for protocol in module.protocols:
            if protocol.name in bound_protocols and protocol.name not in prereq_protocols:
                self.protocols.gen_concept_decl(hpp, protocol)
                hpp.write("\n")

        # Fully define records referenced by bound protocols
        # Needed so implementations of those protocols can use these types
        for record in module.records:
            if record.name in bound_protocol_records:
                self.records.gen_record_decl(hpp, record)
                hpp.write("\n")

        # Generate FULL definitions for records that are type args to bounded records
        # These must be complete so C++ can verify constraints in protocol concepts
        for record in module.records:
            if record.name in early_definition_records:
                if record.name in bound_protocol_records:
                    continue  # Already fully defined above
                self.records.gen_record_decl(hpp, record)
                hpp.write("\n")

        # Forward declare records referenced in protocols (bounds are now available)
        for record_name in sorted(protocol_referenced_records):
            if record_name in module_record_names:
                if record_name in early_definition_records:
                    continue  # Already fully defined above
                if record_name in bound_protocol_records:
                    continue  # Already forward declared above
                if record_name in prereq_protocol_records:
                    continue  # Already forward declared above
                record = records_by_name[record_name]
                if record.type_params:
                    template_header = self.protocols.gen_record_template_header(
                        record.type_params, record.type_param_bounds, record.type_param_kinds
                    )
                    hpp.write(f"{template_header} struct {record_name};\n")
                else:
                    hpp.write(f"struct {record_name};\n")

        if protocol_referenced_records & module_record_names:
            hpp.write("\n")

        # Generate remaining C++20 concepts for user-defined protocols
        for protocol in module.protocols:
            if protocol.name not in bound_protocols and protocol.name not in prereq_protocols:
                self.protocols.gen_concept_decl(hpp, protocol)
                hpp.write("\n")

        # Forward declare remaining records (so global externs can reference them)
        for record in module.records:
            if record.name in protocol_referenced_records:
                continue  # Already forward declared above
            if record.name in early_definition_records:
                continue  # Already fully defined above
            if record.name in bound_protocol_records:
                continue  # Already forward declared above
            if record.name in prereq_protocol_records:
                continue  # Already forward declared above
            if record.type_params:
                # Generic record with bounds/int params: template<Comparable T, std::size_t N> struct SortedList;
                template_header = self.protocols.gen_record_template_header(
                    record.type_params, record.type_param_bounds, record.type_param_kinds
                )
                hpp.write(f"{template_header} struct {record.name};\n")
            else:
                hpp.write(f"struct {record.name};\n")
        if module.records:
            hpp.write("\n")

        # Generate global extern declarations
        # __name__ is always present (synthetic if not user-defined)
        if "__name__" not in seen_globals:
            hpp.write("extern tpy::Global<std::string_view> __name__;\n")
        for stmt in global_decls:
            self.functions.gen_global_extern(hpp, stmt)
        hpp.write("\n")

        # Generate full record definitions (skip those already defined early)
        # Sort by inheritance order so parent classes come before children
        sorted_records = self.records.sort_records_by_inheritance(module.records)
        for record in sorted_records:
            if record.name in early_definition_records:
                continue  # Already fully defined above
            if record.name in bound_protocol_records:
                continue  # Already fully defined above
            self.records.gen_record_decl(hpp, record)
            hpp.write("\n")

        # Generate function declarations
        for func in module.functions:
            self.functions.gen_function_decl(hpp, func)
        hpp.write("\n")

    def _write_header_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write("#pragma once\n\n")
        out.write('#include <tpy/tpy.hpp>\n')
        # Include user module headers (in {mod}.d/{mod}.hpp directories)
        for user_mod in sorted(self.ctx.user_module_imports):
            out.write(f'#include "{user_mod}.d/{user_mod}.hpp"\n')
        out.write("\n")
        out.write(f"namespace tpy_user::{self.ctx.module_name} {{\n\n")

    def _write_source_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write(f'#include "{self.ctx.module_name}.hpp"\n\n')
        out.write(f"namespace tpy_user::{self.ctx.module_name} {{\n\n")

    def _write_header_epilogue(self, out: TextIO) -> None:
        out.write(f"}} // namespace tpy_user::{self.ctx.module_name}\n")
