"""
TurboPython C++ Code Generator

Main orchestrator for generating C++ code from TurboPython AST.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TextIO, TYPE_CHECKING
import io

from ..typesys import TpyType, NamedType, UnionType, OwnType, PendingListType, ListType, ArrayType, IntLiteralType, PtrType, ConstPtrType, BIGINT, clear_native_cpp_names, register_native_cpp_name, register_union_alias
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyVarDecl, VarLinkage

from .context import CodeGenContext, CodeGenOptions, module_to_cpp_namespace, qualified_cpp_name
from .types import TypeResolver
from .protocols import ProtocolGenerator
from .builtins import BuiltinGenerator
from .expressions import ExpressionGenerator
from .statements import StatementGenerator
from .records import RecordGenerator
from .functions import FunctionGenerator
from .type_resolution import resolve_stmt_type_cascade

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
        self.statements = StatementGenerator(self.ctx, self.types, self.builtins, self.protocols)
        self.records = RecordGenerator(self.ctx, self.types, self.protocols)
        self.functions = FunctionGenerator(self.ctx, self.types, self.protocols)

        # Wire up circular dependencies
        self.types.set_protocols(self.protocols)
        self.statements.set_expressions(self.expressions)
        self.records.set_dependencies(self.expressions, self.functions)
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
            actual_user_modules: Set of module names that are actually user modules (have source files).
                                 If None, uses module.user_module_imports (legacy behavior).
            reexported_functions: Dict of {local_name: (source_module, original_name)} for re-exports.
            reexported_records: Dict of {local_name: (source_module, original_name)} for re-exports.
            reexported_variables: Dict of {local_name: (source_module, original_name)} for re-exports.
        """
        self.ctx.module_name = module_name
        self.ctx.source_lines = module.source_lines
        # Populate native C++ name mappings for this module's codegen.
        # Must include both own records and imported records so NamedType.to_cpp()
        # resolves correctly in all type positions (Ptr[Rect] -> SDL_Rect*, etc.)
        clear_native_cpp_names()
        for record in module.records:
            record_info = self.analyzer.registry.get_record(record.name)
            if record_info and record_info.is_native and record_info.native_name:
                register_native_cpp_name(record.name, record_info.native_name)
        for local_name, (src_mod, original_name) in self.analyzer.ctx.user_imported_records.items():
            record_info = self.analyzer.registry.get_record(local_name)
            if record_info and record_info.is_native and record_info.native_name:
                register_native_cpp_name(local_name, record_info.native_name)
            elif not (record_info and record_info.is_native):
                register_native_cpp_name(local_name, qualified_cpp_name(src_mod, original_name))
        # Register imported union type aliases so UnionType.to_cpp() can use
        # the alias name instead of expanding to std::variant<...>
        for local_name, (_src_mod, original_name) in self.analyzer.ctx.user_imported_type_aliases.items():
            alias_type = self.analyzer.registry.get_type_alias(local_name)
            if isinstance(alias_type, UnionType):
                register_union_alias(alias_type.members, local_name)
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
        self.ctx.user_imported_type_aliases = dict(self.analyzer.ctx.user_imported_type_aliases)
        self.ctx.top_level_decls = dict(self.analyzer.ctx.top_level_decls)
        self.ctx.reexported_functions = reexported_functions or {}
        self.ctx.reexported_records = reexported_records or {}
        self.ctx.reexported_variables = reexported_variables or {}
        hpp = io.StringIO()
        cpp = io.StringIO()

        from ..parse.nodes import FunctionLinkage
        # Collect @native (C++ import) functions for out-of-namespace handling
        native_funcs = [f for f in module.functions if f.linkage == FunctionLinkage.NATIVE]

        # Separate global declarations from other top-level statements early
        # (needed for extern declarations in header)
        # Track seen names and types to handle re-declarations (z = 0; z = 5; -> one global, one assignment)
        global_decls = []
        final_decls: list[TpyVarDecl] = []
        native_globals: list[TpyVarDecl] = []
        seen_globals: dict[str, TpyType | None] = {}
        for stmt in module.top_level_stmts:
            if isinstance(stmt, TpyVarDecl):
                if stmt.name not in seen_globals:
                    # Store the type for this global
                    var_type = resolve_stmt_type_cascade(stmt, self.analyzer, self.types)
                    if isinstance(var_type, OwnType):
                        var_type = var_type.wrapped
                    default_int = self.analyzer.ctx.default_int_type
                    if isinstance(var_type, ListType) and isinstance(var_type.element_type, IntLiteralType):
                        var_type = ListType(default_int)
                    elif isinstance(var_type, ArrayType) and isinstance(var_type.element_type, IntLiteralType):
                        var_type = ArrayType(default_int, var_type.size)
                    seen_globals[stmt.name] = var_type
                    if stmt.linkage != VarLinkage.DEFAULT:
                        native_globals.append(stmt)
                    elif stmt.is_final:
                        final_decls.append(stmt)
                    else:
                        global_decls.append(stmt)

        # Track native global name mappings (Python name -> C/C++ name)
        self.ctx.native_global_names = {
            stmt.name: (stmt.native_name or stmt.name)
            for stmt in native_globals
        }

        self._write_header_preamble(hpp, native_funcs, native_globals)
        self._write_source_preamble(cpp)

        # Store global names for use in expression generation (method/field access)
        self.ctx.global_names = set(seen_globals.keys())
        # Track Final globals (constexpr/const at namespace scope)
        self.ctx.final_globals = {stmt.name for stmt in final_decls}
        # Classify globals: non-value-type -> pointer globals (T*)
        # Exclude native globals and final globals
        self.ctx.pointer_globals = {
            name for name, typ in seen_globals.items()
            if typ and not typ.is_value_type()
            and name not in self.ctx.native_global_names
            and name not in self.ctx.final_globals
        }
        # Also include imported non-value-type globals
        for name in self.ctx.user_imported_variables:
            binding = self.ctx.analyzer.global_ns.lookup_local(name)
            if binding and binding.type and not binding.type.is_value_type():
                self.ctx.pointer_globals.add(name)
        # Generate protocol ordering and forward declarations
        self._generate_protocol_ordering(hpp, module, global_decls, final_decls, seen_globals)

        # Generate global definitions in source (before functions)
        for stmt in global_decls:
            self.functions.gen_global_decl(cpp, stmt)
        # Final globals: constexpr in header, const in source (BigInt only)
        for stmt in final_decls:
            self.functions.gen_final_global_source(cpp, stmt)
        cpp.write("\n")

        # Generate function definitions (skip template functions -- defined in header)
        for func in module.functions:
            if self.functions.is_template_function(func):
                continue
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
        # Exclude Final globals from init pre-seeding (they live at namespace scope)
        init_globals = {k: v for k, v in seen_globals.items() if k not in self.ctx.final_globals}
        self.functions.gen_module_init(cpp, module.top_level_stmts, init_globals,
                                       has_user_main=False, module_name=tpy_module_name)
        # Only generate C++ main() for entry point module
        if is_entry_point:
            self.functions.gen_main(cpp)
        else:
            # For non-entry-point modules, just close the namespace
            self.functions.gen_namespace_close(cpp)

        # @native function export definitions go after namespace close (source file)
        # Note: @native is import-only so this is typically empty
        native_exports = [f for f in native_funcs if not f.is_stub]
        if native_exports:
            cpp.write("\n")
            for func in native_exports:
                self.functions.gen_extern_cpp_source_def(cpp, func)
                cpp.write("\n")

        self._write_header_epilogue(hpp)

        return hpp.getvalue(), cpp.getvalue()

    def _generate_protocol_ordering(self, hpp: TextIO, module: TpyModule,
                                    global_decls: list, final_decls: list,
                                    seen_globals: dict) -> None:
        """Generate protocols, forward declarations, and records in proper order.

        C++ requires forward declarations and concepts to be defined before use.
        This method handles the complex ordering requirements.
        """
        deps = self._collect_protocol_deps(module)
        self._generate_forward_decls_and_concepts(hpp, module, deps)
        self._generate_definitions_and_reexports(hpp, module, global_decls, final_decls, seen_globals, deps)

    def _is_native_record(self, record_name: str) -> bool:
        """Check if a record is a native import."""
        record_info = self.analyzer.registry.get_record(record_name)
        return record_info is not None and record_info.is_native

    def _collect_protocol_deps(self, module: TpyModule) -> _ProtocolDeps:
        """Collect all dependency sets needed for protocol ordering."""
        # Exclude native records -- they don't generate C++ structs
        non_native = [r for r in module.records if not self._is_native_record(r.name)]
        deps = _ProtocolDeps(
            module_record_names={r.name for r in non_native},
            records_by_name={r.name: r for r in non_native},
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

    def _emit_concept_and_dynamic(self, hpp: TextIO, protocol: 'TpyProtocol') -> None:
        """Emit concept for a protocol, plus base/adapter if @dynamic."""
        from ..parse import TpyProtocol as _TP
        self.protocols.gen_concept_decl(hpp, protocol)
        if protocol.is_dynamic:
            hpp.write("\n")
            self.protocols.gen_dynamic_base_and_adapter(hpp, protocol)

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
                self._emit_concept_and_dynamic(hpp, protocol)
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
                self._emit_concept_and_dynamic(hpp, protocol)
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
                self._emit_concept_and_dynamic(hpp, protocol)
                hpp.write("\n")

    def _generate_definitions_and_reexports(
        self, hpp: TextIO, module: TpyModule,
        global_decls: list, final_decls: list, seen_globals: dict, deps: _ProtocolDeps
    ) -> None:
        """Generate remaining forward decls, global externs, record definitions, functions, and re-exports."""
        # Forward declare remaining records (excluding native records)
        emitted_fwd = False
        for record in module.records:
            if self._is_native_record(record.name):
                continue
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
            emitted_fwd = True
        # Ensure spacing between forward decl section and externs when records exist
        has_non_native_records = any(not self._is_native_record(r.name) for r in module.records)
        if emitted_fwd or has_non_native_records:
            hpp.write("\n")

        # Global extern declarations
        for stmt in global_decls:
            self.functions.gen_global_extern(hpp, stmt)
        # Final global declarations (inline constexpr or extern const)
        for stmt in final_decls:
            self.functions.gen_final_global_header(hpp, stmt)
        hpp.write("\n")

        # Imported type alias using-declarations (before function forward
        # decls so signatures can reference alias names like Shape)
        emitted_imported_alias = False
        for local_name, (src_mod, original_name) in sorted(self.ctx.user_imported_type_aliases.items()):
            qualified = qualified_cpp_name(src_mod, original_name)
            if local_name == original_name:
                hpp.write(f"using {qualified};\n")
            else:
                hpp.write(f"using {local_name} = {qualified};\n")
            emitted_imported_alias = True
        if emitted_imported_alias:
            hpp.write("\n")

        # Function forward declarations (before records, so inline
        # constructor/method bodies can call free functions)
        emitted_fwd_func = False
        for func in module.functions:
            if self.functions.gen_function_forward_decl(hpp, func):
                emitted_fwd_func = True
        if emitted_fwd_func:
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

        # Module-local type alias definitions (after record definitions
        # so member types are complete for std::variant)
        emitted_alias = False
        for name, (typ, _loc) in sorted(module.type_aliases.items()):
            cpp_type = self.types.type_to_cpp(typ)
            hpp.write(f"using {name} = {cpp_type};\n")
            emitted_alias = True
        if emitted_alias:
            hpp.write("\n")
        # Register module-local union aliases AFTER emitting the using
        # declaration (to avoid circular `using Shape = Shape;`) but
        # BEFORE function definitions (so signatures use the alias name)
        for name, (typ, _loc) in module.type_aliases.items():
            if isinstance(typ, UnionType):
                register_union_alias(typ.members, name)

        # Function declarations (template definitions, stubs, and extern "C";
        # non-template signatures are already forward-declared above)
        emitted_func_decl = False
        for func in module.functions:
            if self.functions.gen_function_decl(hpp, func):
                emitted_func_decl = True

        # Re-declare imported C-linkage functions in this namespace so they're
        # visible without cross-module namespace qualification. This is legal
        # because extern "C" functions can be declared multiple times.
        # Applies to @native_c imports and @extern_c exports.
        # Skip functions that are also re-exports (handled below to avoid duplicates).
        for local_name in sorted(self.ctx.user_imported_functions):
            if local_name in self.ctx.reexported_functions:
                continue
            func_info = self.ctx.analyzer.registry.get_function(local_name)
            if func_info and (func_info.is_native_c or func_info.is_extern_c):
                self.functions.gen_extern_c_redecl(hpp, func_info)
                emitted_func_decl = True
        if emitted_func_decl:
            hpp.write("\n")

        # Re-exported functions
        if self.ctx.reexported_functions:
            for local_name, (source_module, original_name) in sorted(self.ctx.reexported_functions.items()):
                # For C-linkage functions, emit an extern "C" re-declaration.
                # Using/alias re-exports don't work because the C++ name may
                # differ from the Python name (e.g., @native_c("SDL_GetTicks") def get_ticks).
                func_info = self.ctx.analyzer.registry.get_function(local_name)
                if func_info and (func_info.is_native_c or func_info.is_extern_c):
                    self.functions.gen_extern_c_redecl(hpp, func_info)
                    continue
                qualified = qualified_cpp_name(source_module, original_name)
                if local_name == original_name:
                    hpp.write(f"using {qualified};\n")
                else:
                    hpp.write(f"inline auto& {local_name} = {qualified};\n")
            hpp.write("\n")

        # Re-exported records
        if self.ctx.reexported_records:
            for local_name, (source_module, original_name) in sorted(self.ctx.reexported_records.items()):
                qualified = qualified_cpp_name(source_module, original_name)
                if local_name == original_name:
                    hpp.write(f"using {qualified};\n")
                else:
                    hpp.write(f"using {local_name} = {qualified};\n")
            hpp.write("\n")

        # Re-exported variables
        if self.ctx.reexported_variables:
            for local_name, (source_module, original_name) in sorted(self.ctx.reexported_variables.items()):
                qualified = qualified_cpp_name(source_module, original_name)
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

    def _write_header_preamble(self, out: TextIO,
                               native_funcs: list[TpyFunction] | None = None,
                               native_globals: list[TpyVarDecl] | None = None) -> None:
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
                if parent_pkg == self.ctx.module_name:
                    continue  # don't self-include
                if parent_pkg in self.ctx.all_user_modules and parent_pkg not in included:
                    include_path = self._module_to_include_path(parent_pkg)
                    out.write(f'#include "{include_path}"\n')
                    included.add(parent_pkg)
            # Then include the module itself (skip self-include)
            if user_mod != self.ctx.module_name and user_mod not in included:
                include_path = self._module_to_include_path(user_mod)
                out.write(f'#include "{include_path}"\n')
                included.add(user_mod)
        out.write("\n")

        # @native (C++ import) declarations go before the tpy_user namespace
        if native_funcs:
            for func in native_funcs:
                self.functions.gen_native_header_decl(out, func)
            out.write("\n")

        # Native global extern declarations go before the tpy_user namespace
        if native_globals:
            for stmt in native_globals:
                cpp_name = stmt.native_name or stmt.name
                var_type = resolve_stmt_type_cascade(stmt, self.analyzer, self.types)
                if stmt.linkage == VarLinkage.NATIVE_C_ARRAY:
                    # C array global: Ptr[T] -> extern "C" T name[];
                    # The incomplete array type decays to T* when used.
                    if isinstance(var_type, (PtrType, ConstPtrType)):
                        elem_cpp = var_type.pointee.to_cpp()
                    else:
                        elem_cpp = var_type.to_cpp()
                    out.write(f'extern "C" {elem_cpp} {cpp_name}[];\n')
                elif stmt.linkage == VarLinkage.NATIVE_C:
                    cpp_type = var_type.to_cpp()
                    out.write(f'extern "C" {cpp_type} {cpp_name};\n')
                else:
                    cpp_type = var_type.to_cpp()
                    ns, bare = FunctionGenerator._split_native_name(cpp_name)
                    if ns:
                        out.write(f"namespace {ns} {{ extern {cpp_type} {bare}; }}\n")
                    else:
                        out.write(f"extern {cpp_type} {bare};\n")
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
