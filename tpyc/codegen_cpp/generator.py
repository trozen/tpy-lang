"""
TurboPython C++ Code Generator

Main orchestrator for generating C++ code from TurboPython AST.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, TextIO, TYPE_CHECKING
import io
import sys as _sys

from ..typesys import TpyType, NominalType, UnionType, OwnType, PendingListType, PtrType, NoneType, VoidType, BIGINT, RecordInfo, ProtocolInfo, clear_codegen_state, register_native_cpp_name, register_union_alias, resolve_int_literals, _native_cpp_names, is_void_like_type, bare_name
from ..type_def_registry import type_def_of, is_enum_type, enum_info_of, protocol_info_of
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyVarDecl, VarLinkage
from ..parse.nodes import TpyTupleUnpack, ModuleDirectives

from .context import CodeGenContext, CodeGenOptions, module_to_cpp_namespace, module_has_cpp_namespace_override, qualified_cpp_name, qualify_native_name, escape_cpp_string, escape_cpp_char, escape_cpp_name
from .types import TypeResolver
from .protocols import ProtocolGenerator
from .builtins import BuiltinGenerator
from .expressions import ExpressionGenerator
from .statements import StatementGenerator
from .records import RecordGenerator
from .functions import FunctionGenerator
from .type_resolution import resolve_stmt_type_cascade
from .string_dispatch import find_best_discriminator, STRING_SWITCH_THRESHOLD
from ..symbol_binding import SymbolKind

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer


def _emits_own_cpp(typ: NominalType) -> bool:
    """True if this NominalType's C++ emission bypasses its Python name --
    either via a TypeDef cpp_formatter (builtins like list, str), via a
    native_name mapping (@native records), or because it has no runtime
    form (compile-time-only types like FStr). In those cases, an imported
    `using PythonName = ...;` alias is dead code: the Python name never
    appears in generated C++."""
    td = type_def_of(typ)
    if td is not None and (td.cpp_formatter is not None or td.is_compile_time_only):
        return True
    return typ.name in _native_cpp_names

# Maps user-facing platform names to sys.platform prefixes (also in compiler.py)
_PLATFORM_MAP = {"windows": "win32", "linux": "linux", "macos": "darwin"}


def _platform_matches(platform_filter: str | None) -> bool:
    """Check if a platform filter matches the current platform."""
    if platform_filter is None:
        return True
    mapped = _PLATFORM_MAP.get(platform_filter.lower(), platform_filter)
    return _sys.platform.startswith(mapped)


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


def _references_nested_type(typ: TpyType) -> bool:
    """Check if a type references a nested type (dotted name)."""
    if (isinstance(typ, NominalType) or is_enum_type(typ)) and "." in typ.name:
        return True
    return any(_references_nested_type(inner) for inner in typ.inner_types())


def _func_uses_nested_type(func: TpyFunction) -> bool:
    """Check if a function's signature references any nested type."""
    for _, ptype in func.params:
        if _references_nested_type(ptype):
            return True
    return _references_nested_type(func.return_type)


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

        # Create component generators (ordered by dependencies)
        self.protocols = ProtocolGenerator(self.ctx)
        self.types = TypeResolver(self.ctx, self.protocols)
        self.builtins = BuiltinGenerator(self.ctx, self.types)
        self.expressions = ExpressionGenerator(self.ctx, self.types, self.builtins, self.protocols)
        self.statements = StatementGenerator(self.ctx, self.types, self.builtins, self.protocols, self.expressions)
        self.functions = FunctionGenerator(self.ctx, self.types, self.protocols, self.statements)
        self.records = RecordGenerator(self.ctx, self.types, self.protocols, self.expressions, self.functions)

        # Generator codegen (must be created after wiring since it uses statements/functions)
        from .gen_generators import GeneratorCodegen
        self.gen_generators = GeneratorCodegen(
            self.ctx, self.types, self.expressions, self.statements, self.functions)
        self.records.gen_generators = self.gen_generators

        # Async coroutine codegen (state-machine struct + Poll<T> poll())
        from .gen_async import AsyncCoroCodegen
        self.gen_async = AsyncCoroCodegen(
            self.ctx, self.types, self.expressions, self.statements, self.functions)

    def generate(self, module: TpyModule, module_name: str = "generated",
                 is_entry_point: bool = True,
                 actual_user_modules: set[str] | None = None,
                 implicit_stdlib_modules: set[str] | None = None,
                 cycle_peers: 'frozenset[str] | None' = None) -> tuple[str, str]:
        """Generate C++ header and source files.

        Args:
            module: The parsed TurboPython module AST.
            module_name: Name for the generated files (used in #include).
            is_entry_point: True if this is the entry point module (generates main()).
            actual_user_modules: Set of module names that are actually user modules (have source files).
                                 If None, uses module.user_module_imports (legacy behavior).
        """
        self.ctx.module_name = module_name
        self.ctx.source_lines = module.source_lines
        self.ctx.cycle_peers = cycle_peers or frozenset()
        # Filter out keyword stubs (@builtin_type classes / @builtin_decorator functions
        # that exist only for import resolution -- no C++ code needed).
        module.records = [
            r for r in module.records
            if not (ri := self.analyzer.registry.get_record(r.name)) or not ri.is_keyword_stub
        ]
        module.functions = [
            f for f in module.functions if not f.builtin_decorator_key
        ]
        # Populate native C++ name mappings for this module's codegen.
        # Must include both own records and imported records so NominalType.to_cpp()
        # resolves correctly in all type positions (Ptr[Rect] -> SDL_Rect*, etc.)
        clear_codegen_state()
        for record in module.records:
            record_info = self.analyzer.registry.get_record(record.name)
            if record_info and record_info.is_native and record_info.native_name:
                register_native_cpp_name(record.name, record_info.native_name)
        # @native enums: register their canonical C++ qname so type-position
        # renderings (`Optional[E]`, `list[E]`, function signatures) resolve
        # to `ns::E` instead of `tpyapp::<module>::E`.
        for enum in module.enums:
            if not enum.is_native:
                continue
            enum_type = self.analyzer.registry.get_enum(enum.name)
            if enum_type is None:
                continue
            einfo = enum_info_of(enum_type)
            if einfo is not None and einfo.native_name:
                register_native_cpp_name(enum.name, einfo.native_name)
        # Register nested type names: "Outer.Inner" -> "Outer::Inner" for C++ qualified access.
        # Use _native_cpp_names directly to avoid ensure_qualified adding "::" prefix
        # (these are module-local types, not cross-module references).
        for record in module.all_records():
            if "." in record.name:
                _native_cpp_names[record.name] = record.name.replace(".", "::")
        for enum in module.all_enums():
            if "." in enum.name:
                _native_cpp_names[enum.name] = enum.name.replace(".", "::")
        # Register C++ names for imported records/enums using their canonical
        # (declaring-module) qname. NominalType.to_cpp() consults these when it
        # has no qname-based formatter on hand. Iterate the full record/enum
        # registries; imported_*_qualification filters out locals and builtins.
        current_module = self.analyzer.ctx.module_name
        for local_name, record_info in self.analyzer.registry.records.items():
            if record_info.is_native and record_info.native_name:
                register_native_cpp_name(local_name, record_info.native_name)
                continue
            if record_info.is_native:
                continue
            qual = self.analyzer.registry.imported_record_qualification(
                local_name, current_module)
            if qual is not None:
                register_native_cpp_name(local_name, qualified_cpp_name(*qual))
        # Cross-module @dynamic protocols (e.g. `Awaker` imported into
        # `asyncio._executor` from `tpy.coro`) need the same `_native_cpp_names`
        # qualification as user records: `NominalType.to_cpp()` consults the
        # map for protocol short names too. Locally-defined @dynamic protocols
        # skip this registration so same-module references emit the bare name.
        # Static protocols are excluded -- they monomorphize and never surface
        # as a runtime C++ type.
        for local_name, proto_info in self.analyzer.registry._protocols_by_local_name.items():
            # @native + @dynamic protocols (e.g. `Throwable` which lives in
            # runtime/cpp/include/tpy/throwable.hpp as ::tpy::Throwable) get
            # their cpp_concept name as the C++ rendering, both for the
            # defining module and for any module that imports them. The
            # imported_protocol_qualification path would otherwise route
            # through the codegen-emitted namespace (`tpystd::tpy::Throwable`)
            # which doesn't exist for @native+@dynamic protocols.
            if proto_info is not None and proto_info.cpp_concept and proto_info.is_dynamic:
                register_native_cpp_name(local_name, proto_info.cpp_concept)
                continue
            qual = self.analyzer.registry.imported_protocol_qualification(
                local_name, current_module)
            if qual is not None:
                register_native_cpp_name(local_name, qualified_cpp_name(*qual))
        # `submod.X` (after `from pkg import submod`) doesn't import X by
        # short name, so the loop above misses it. Walk each registered
        # dep module's exports to register cross-module qualified names.
        # Locally-defined record short names are excluded so a same-named
        # peer record in a registered dep module never shadows the local
        # `Foo`'s unqualified emission. Without this guard, a workspace
        # that registers all modules into a shared dict (or any path that
        # transitively pulls a peer module with a colliding short name)
        # would misqualify the local record.
        # Two cross-module record cases handled in one walk:
        #   (a) Plain user records imported from a peer module -- qualify
        #       through `record_info.module`.
        #   (b) `@builtin_type`-with-body records (Task and future
        #       siblings) whose stub claims a well-known qname like
        #       `tpy.Task` but whose body lives in a `# tpy: cpp_namespace`-
        #       tagged module (e.g. `asyncio._executor`). These would
        #       otherwise mis-qualify as `tpyapp::<module>::Name`, so the
        #       defining_module + cpp_namespace override path is required.
        # Common skips (already registered, local shadow, @native rename)
        # apply to both; the case split determines which module to qualify
        # through.
        local_short_record_names = {r.name for r in module.records}
        for dep_module in self.analyzer.registry.modules.values():
            if dep_module.name == current_module:
                continue
            for short, record_info in dep_module.records.items():
                if short in _native_cpp_names:
                    continue
                if short in local_short_record_names:
                    continue
                if record_info.is_native:
                    # @native rename handled above; skip whether or not
                    # native_name is set.
                    continue
                if record_info.builtin_type_key:
                    # Case (b)
                    if record_info.is_keyword_stub:
                        continue
                    defining = record_info.defining_module
                    if not defining:
                        continue
                    if not module_has_cpp_namespace_override(defining):
                        continue
                    register_native_cpp_name(
                        short, qualified_cpp_name(defining, short))
                else:
                    # Case (a)
                    if dep_module.is_builtin:
                        continue
                    if record_info.module is None or record_info.module == current_module:
                        continue
                    register_native_cpp_name(
                        short, qualified_cpp_name(record_info.module, short))
        for local_name in list(self.analyzer.registry.enums.keys()):
            qual = self.analyzer.registry.imported_enum_qualification(
                local_name, current_module)
            if qual is None:
                continue
            # @native imported enums: use their canonical C++ qname, not
            # the module-qualified spelling.
            enum_type = self.analyzer.registry.get_enum(local_name)
            einfo = enum_info_of(enum_type) if enum_type is not None else None
            if einfo is not None and einfo.is_native and einfo.native_name:
                qualified = einfo.native_name
            else:
                qualified = qualified_cpp_name(*qual)
            register_native_cpp_name(local_name, qualified)
            # For aliased imports, also map the canonical name so references
            # that go through NominalType.name resolve too.
            original_name = qual[1]
            if local_name != original_name:
                register_native_cpp_name(original_name, qualified)
        # Register native names from builtin type records without type_factory
        # (e.g. TextIO -> tpy::TextFile, ValueError -> tpy::ValueError) so
        # NominalType.to_cpp() resolves even when the type isn't explicitly imported.
        # Skip names that shadow local record definitions in the current module.
        local_record_names = {r.name for r in module.records}
        for record_info in self.analyzer.registry.get_native_builtin_records():
            if record_info.name not in local_record_names:
                register_native_cpp_name(record_info.name, record_info.native_name)
        # Register imported union type aliases so UnionType.to_cpp() can use
        # the alias name instead of expanding to std::variant<...>. Skip
        # recursive aliases here; the iter_imported_recursive_unions loop
        # below registers them with their qualified C++ name so cross-module
        # consumers emit the right type.
        for local_name, (source_module, original_name) in self.analyzer.registry.imported_type_alias_info.items():
            alias_type = self.analyzer.registry.get_type_alias(local_name)
            if not isinstance(alias_type, UnionType):
                continue
            source_info = self.analyzer.registry.modules.get(source_module)
            if source_info is not None and original_name in source_info.recursive_union_names:
                continue
            register_union_alias(alias_type.members, local_name)
        # Filter user_module_imports to only include actual user modules (not builtins without user files)
        if actual_user_modules is not None:
            self.ctx.user_module_imports = {k: v for k, v in module.user_module_imports.items() if k in actual_user_modules}
            self.ctx.all_user_modules = actual_user_modules
        else:
            self.ctx.user_module_imports = module.user_module_imports
            self.ctx.all_user_modules = set(module.user_module_imports.keys())
        self.ctx.implicit_stdlib_modules = implicit_stdlib_modules or set()
        self.ctx.top_level_decls = dict(self.analyzer.ctx.top_level_decls)
        self.ctx.macro_dep_modules = set(self.analyzer.ctx.macro_dep_modules)
        self.ctx.init_recursive_unions(
            module.recursive_union_names,
            module.type_aliases,
            imported_modules=self.analyzer.registry.modules,
        )
        # Register recursive union aliases so record field rendering resolves
        # e.g. Box[UnionType(Lit, BinOp)] -> Box<Expr>. Local aliases use the
        # unqualified name; imported aliases are qualified to the defining
        # module's namespace so cross-module consumers emit the right C++ type.
        for name in module.recursive_union_names:
            entry = module.type_aliases.get(name)
            if entry is not None:
                typ = entry[0]
                if isinstance(typ, UnionType):
                    register_union_alias(typ.members, name)
        for info in self.ctx.iter_imported_recursive_unions():
            register_union_alias(
                info.full_members,
                qualified_cpp_name(info.origin, info.name),
            )
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
                    var_type = resolve_int_literals(var_type, self.analyzer.ctx.default_int_for_literal)
                    seen_globals[stmt.name] = var_type
                    if stmt.linkage != VarLinkage.DEFAULT:
                        native_globals.append(stmt)
                    elif stmt.is_final:
                        final_decls.append(stmt)
                    else:
                        global_decls.append(stmt)
            elif isinstance(stmt, TpyTupleUnpack):
                for i, name in enumerate(stmt.targets):
                    if name is None or name in seen_globals:
                        continue
                    var_type = stmt.target_types[i]
                    if isinstance(var_type, OwnType):
                        var_type = var_type.wrapped
                    seen_globals[name] = var_type
                    synthetic = TpyVarDecl(
                        name=name, type=var_type, init=None, loc=stmt.loc)
                    global_decls.append(synthetic)

        # Track native global name mappings (Python name -> C/C++ name)
        self.ctx.native_global_names = {
            stmt.name: (stmt.native_name or stmt.name)
            for stmt in native_globals
        }

        if module.directives.native_module:
            # Native modules are declaration-only -- no .hpp or .cpp generated.
            # Their # tpy: include() directives are propagated to importing modules.
            return "", ""

        self._write_header_preamble(hpp, native_funcs, native_globals, directives=module.directives)

        self._write_source_preamble(cpp, module)

        # Store global names for use in expression generation (method/field access)
        self.ctx.global_names = set(seen_globals.keys())
        # Track Final globals (constexpr/const at namespace scope)
        self.ctx.final_globals = {stmt.name for stmt in final_decls}
        # Classify globals: non-value-type -> pointer globals (T*)
        # Exclude native globals and final globals
        self.ctx.pointer_globals = {
            name for name, typ in seen_globals.items()
            if typ and not typ.is_value_type()
            and not self.ctx.is_recursive_union(typ)
            and name not in self.ctx.native_global_names
            and name not in self.ctx.final_globals
        }
        # Also include imported non-value-type globals
        for name, cell in (self.analyzer.ctx.module_attributes or {}).items():
            bd = cell.binding
            if bd.kind != SymbolKind.VARIABLE or bd.defining_module is None:
                continue
            binding = self.ctx.analyzer.global_ns.lookup_local(name)
            if binding and binding.type and not binding.type.is_value_type() and not self.ctx.is_recursive_union(binding.type):
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
            if func.skip_codegen:
                continue
            if func.is_generator:
                if not self.gen_generators.is_simple_generator(func):
                    self.gen_generators.gen_generator_next(cpp, func)
                    cpp.write("\n")
                    self.gen_generators.gen_generator_factory(cpp, func)
                    cpp.write("\n")
                # Simple generators are defined inline in the header
                continue
            if func.is_async:
                # Templates are emitted inline in the header (same rule as
                # template functions). For non-template async coros, emit
                # poll() body, optional __finally_top(), and the factory
                # function in the .cpp.
                if not func.type_params:
                    self.gen_async.gen_coro_poll_def(cpp, func)
                    cpp.write("\n")
                    self.gen_async.gen_coro_finally_top_def(cpp, func)
                    cpp.write("\n")
                    self.gen_async.gen_factory(cpp, func)
                    cpp.write("\n")
                continue
            if self.functions.is_template_function(func):
                continue
            self.functions.gen_function_def(cpp, func)
            cpp.write("\n")

        # Method generator __next__() / async __poll__() definitions
        for record in module.all_records():
            for method in record.methods:
                if method.is_generator and not self.gen_generators.is_simple_generator(method):
                    self.gen_generators.gen_generator_next(
                        cpp, method, record_name=record.name)
                    cpp.write("\n")
                elif method.is_async and not method.type_params:
                    # Non-template async methods: poll body lands in .cpp.
                    # Template async methods have their poll body emitted
                    # inline next to the struct in the .hpp (see above).
                    self.gen_async.gen_coro_poll_def(
                        cpp, method, record_name=record.name)
                    cpp.write("\n")
                    self.gen_async.gen_coro_finally_top_def(
                        cpp, method, record_name=record.name)
                    cpp.write("\n")

        # Non-trivial method bodies live in the .cpp (matches free-function
        # placement). Trivial bodies stay in the .hpp -- emitted earlier by
        # the matching `mode="def_hpp"` pass in `_generate_protocol_ordering`.
        for record in self.records.sort_records_by_inheritance(module.records):
            self.records.gen_record_method_defs(cpp, record, mode="def_cpp")

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
            self.functions.gen_main(cpp, no_main=self.options.no_main)
        else:
            # For non-entry-point modules, just close the namespace
            self.functions.gen_namespace_close(cpp)

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
        # Emit before forward decls/concepts so inline references to imported
        # names are in scope (e.g. struct member initializers using `Rc<T>`).
        self._gen_reexport_using_decls(hpp)
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
                    proto_info = protocol_info_of(bound)
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
                    proto_info = protocol_info_of(bound)
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

    def _emit_hash_specialization(self, hpp: TextIO, record: TpyRecord) -> None:
        """Emit std::hash specialization for a hashable record.

        Called immediately after the record's struct definition so that
        subsequent records can use it as a set/dict-key element type.
        `@native`-renamed records use `record_info.native_name` as the
        qualified target (mirrors `_emit_value_type_spec`).
        """
        info = self.analyzer.registry.get_record(record.name)
        if not info or "__hash__" not in info.methods:
            return
        if record.type_params or record.builtin_type_key:
            return
        ns = module_to_cpp_namespace(self.ctx.module_name)
        cpp_name = (qualify_native_name(info.native_name)
                    if info.native_name
                    else qualified_cpp_name(self.ctx.module_name, record.name))
        hpp.write(f"}} // namespace {ns}\n\n")
        hpp.write(f"template<> struct std::hash<{cpp_name}> {{\n")
        hpp.write(f"    size_t operator()(const {cpp_name}& val) const noexcept {{\n")
        hpp.write(f"        return static_cast<size_t>(::tpy::__hash__(val));\n")
        hpp.write(f"    }}\n")
        hpp.write(f"}};\n")
        hpp.write(f"\nnamespace {ns} {{\n\n")

    def _emit_nested_hash_specializations(self, hpp: TextIO, record: 'TpyRecord') -> None:
        """Emit std::hash specializations for nested records (recursively).

        Skips `@native` nested records -- those are emitted by the
        dedicated native-records pass in `_generate_definitions_and_reexports`.
        Symmetric with `_emit_nested_value_type_specs`.
        """
        for nested_rec in record.nested_records:
            if self._is_native_record(nested_rec.name):
                continue
            self._emit_hash_specialization(hpp, nested_rec)
            self._emit_nested_hash_specializations(hpp, nested_rec)

    def _emit_value_type_spec(self, hpp: TextIO, record: TpyRecord) -> None:
        """Emit `tpy::is_value_type<T>` spec for a ValueType record.

        Called immediately after the record's struct definition so any
        subsequent inline method body / templated call instantiating on
        the record sees the spec. `@native`-renamed records use
        `record_info.native_name` as the qualified C++ type (the rename
        target lives outside the module's namespace).

        Note: no `builtin_type_key` guard, asymmetric with
        `_emit_hash_specialization`. `@builtin_type` ValueType records
        (e.g. `Waker`) rely on this emission today -- there's no
        hand-written spec in `runtime/cpp/include/tpy/type_traits.hpp`
        for them, so the codegen path is load-bearing. Revisit if a
        future `@builtin_type` + `@native` ValueType adds a hand-written
        spec; the resulting duplicate `template<>` would be a hard C++
        error pointing here.
        """
        record_info = self.analyzer.registry.get_record(record.name)
        if not record_info or not record_info.is_value_type:
            return
        ns = module_to_cpp_namespace(self.ctx.module_name)
        base_cpp_name = (qualify_native_name(record_info.native_name)
                         if record_info.native_name
                         else qualified_cpp_name(self.ctx.module_name, record.name))
        hpp.write(f"}} // namespace {ns}\n\n")
        if record.type_params:
            tparams_decl = ", ".join(
                f"std::size_t {tp}" if (i < len(record_info.type_param_kinds)
                    and record_info.type_param_kinds[i].name == "INT")
                else f"typename {tp}"
                for i, tp in enumerate(record.type_params)
            )
            tparams_use = ", ".join(record.type_params)
            hpp.write(f"template<{tparams_decl}> struct tpy::is_value_type<{base_cpp_name}<{tparams_use}>> : std::true_type {{}};\n")
        else:
            hpp.write(f"template<> struct tpy::is_value_type<{base_cpp_name}> : std::true_type {{}};\n")
        hpp.write(f"\nnamespace {ns} {{\n\n")

    def _emit_nested_value_type_specs(self, hpp: TextIO, record: 'TpyRecord') -> None:
        """Emit value-type specs for nested records (recursively).

        Skips `@native` nested records -- those are emitted by the
        dedicated native-records pass in `_generate_definitions_and_reexports`.
        Emitting here too would produce a duplicate `template<>` (hard C++
        error). Parser today rejects `@native` nested in `@native` but
        permits `@native` nested in a non-native outer, so the duplicate
        path is reachable without this guard.
        """
        for nested_rec in record.nested_records:
            if self._is_native_record(nested_rec.name):
                continue
            self._emit_value_type_spec(hpp, nested_rec)
            self._emit_nested_value_type_specs(hpp, nested_rec)

    def _emit_concept_and_dynamic(self, hpp: TextIO, protocol: 'TpyProtocol') -> None:
        """Emit concept for a protocol, plus base class if @dynamic.

        Adapter specializations (::tpy::Adapter, ::tpy::RefAdapter) are emitted
        separately at global scope via _gen_dynamic_adapter_specs().
        """
        from ..parse import TpyProtocol as _TP
        emitted = self.protocols.gen_concept_decl(hpp, protocol)
        if not emitted:
            return
        if protocol.is_dynamic:
            hpp.write("\n")
            self.protocols.gen_dynamic_base_class(hpp, protocol)
        hpp.write("\n")

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

        # Fully define records referenced by bound protocols
        for record in module.records:
            if record.name in deps.bound_protocol_records:
                self.records.gen_record_decl(hpp, record)
                self._emit_hash_specialization(hpp, record)
                self._emit_nested_hash_specializations(hpp, record)
                self._emit_value_type_spec(hpp, record)
                self._emit_nested_value_type_specs(hpp, record)
                hpp.write("\n")

        # Full definitions for records that are type args to bounded records
        for record in module.records:
            if record.name in deps.early_definition_records:
                if record.name in deps.bound_protocol_records:
                    continue
                self.records.gen_record_decl(hpp, record)
                self._emit_hash_specialization(hpp, record)
                self._emit_nested_hash_specializations(hpp, record)
                self._emit_value_type_spec(hpp, record)
                self._emit_nested_value_type_specs(hpp, record)
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

    def _generate_definitions_and_reexports(
        self, hpp: TextIO, module: TpyModule,
        global_decls: list, final_decls: list, seen_globals: dict, deps: _ProtocolDeps
    ) -> None:
        """Generate remaining forward decls, global externs, record definitions, functions, and re-exports."""
        # Hash + is_value_type specs for @native records. The main per-record
        # emit path (`sort_records_by_inheritance`) filters native records out,
        # so without this pass `val_or_ref_t<NativeT>` lands on the false-type
        # default (T&) in generic instantiations -- wrong ABI for a value type --
        # and any TPy-declared `__hash__` on a native record loses its
        # `std::hash` spec, breaking dict/set use. Emitted before any record
        # body / function body that might instantiate on the native type.
        for record in module.all_records():
            if self._is_native_record(record.name):
                self._emit_hash_specialization(hpp, record)
                self._emit_value_type_spec(hpp, record)

        # Enum class declarations (before records, since records may have enum fields).
        # @native enums skip the declaration entirely -- the user's
        # `# tpy: include(...)` directive provides the C++ enum class.
        for enum in module.enums:
            if enum.is_native:
                continue
            self._gen_enum_decl(hpp, enum)

        # Dynamic protocol adapter specs and EnumUtil specs must be at global scope.
        # Only emit EnumUtil for top-level enums here; nested enums need
        # their parent struct defined first (emitted after record definitions).
        # @native enums still get EnumUtil (reflection across the binding
        # boundary) but no operator<< -- that's emitted by the user if
        # needed, and TPy's print/repr paths route through __repr__ via
        # the runtime template gated on EnumUtil presence.
        dynamic_protocols = [p for p in module.protocols if p.is_dynamic]
        top_enums = module.enums
        if dynamic_protocols or top_enums:
            ns = module_to_cpp_namespace(self.ctx.module_name)
            hpp.write(f"}} // namespace {ns}\n\n")
            self._gen_dynamic_adapter_specs(hpp, module, dynamic_protocols)
            self._gen_enum_util_decls_for(hpp, top_enums)
            hpp.write(f"namespace {ns} {{\n\n")
            for enum in top_enums:
                if enum.is_native:
                    continue
                self._gen_enum_operator_ostream(hpp, enum)

        # Forward declare recursive union wrapper structs (before records,
        # so that record fields like Box[JsonValue] can reference the name)
        emitted_fwd = False
        for name, (_typ, _loc) in module.type_aliases.items():
            if name in module.recursive_union_names:
                hpp.write(f"struct {name};\n")
                emitted_fwd = True

        # Forward declare remaining records (excluding native records)
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
        for local_name, (src_mod, original_name) in sorted(
                self.analyzer.registry.imported_type_alias_info.items()):
            # Skip aliases whose C++ emission bypasses the Python name --
            # the `using PythonName = ...;` would be dead code. See
            # _emits_own_cpp above.
            alias_type = self.analyzer.registry.get_type_alias(local_name)
            if alias_type is not None:
                if isinstance(alias_type, NominalType) and _emits_own_cpp(alias_type):
                    continue
                if not isinstance(alias_type, (NominalType, UnionType)):
                    continue
            qualified = qualified_cpp_name(src_mod, original_name)
            if local_name == original_name:
                hpp.write(f"using {qualified};\n")
            else:
                hpp.write(f"using {local_name} = {qualified};\n")
            emitted_imported_alias = True
        if emitted_imported_alias:
            hpp.write("\n")

        # Generator struct forward declarations (so factory forward decls
        # can reference the struct type name).
        emitted_gen_fwd = False
        for func in module.functions:
            if func.skip_codegen:
                continue
            if func.is_generator and not self.gen_generators.is_simple_generator(func):
                self.gen_generators.gen_generator_forward_decl(hpp, func)
                emitted_gen_fwd = True
            if func.is_async:
                self.gen_async.gen_coro_forward_decl(hpp, func)
                emitted_gen_fwd = True
        # Method generator/async struct forward declarations (before records)
        for record in module.all_records():
            for method in record.methods:
                if method.is_generator and not self.gen_generators.is_simple_generator(method):
                    self.gen_generators.gen_generator_forward_decl(
                        hpp, method, record_name=record.name)
                    emitted_gen_fwd = True
                if method.is_async:
                    self.gen_async.gen_coro_forward_decl(
                        hpp, method, record_name=record.name)
                    emitted_gen_fwd = True
        if emitted_gen_fwd:
            hpp.write("\n")

        # Function forward declarations (before records, so inline
        # constructor/method bodies can call free functions).
        # Functions whose signatures reference nested types are deferred
        # until after record definitions (the parent struct must be complete).
        deferred_fwd_funcs: list[TpyFunction] = []
        emitted_fwd_func = False
        for func in module.functions:
            if func.skip_codegen:
                continue
            if func.is_generator:
                if self.gen_generators.is_simple_generator(func):
                    continue  # Simple generators are inline -- no forward decl
                if self.gen_generators.gen_generator_factory_forward_decl(hpp, func):
                    emitted_fwd_func = True
            elif func.is_async:
                if self.gen_async.gen_factory_forward_decl(hpp, func):
                    emitted_fwd_func = True
            elif _func_uses_nested_type(func):
                deferred_fwd_funcs.append(func)
            elif self.functions.gen_function_forward_decl(hpp, func):
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
            self._emit_hash_specialization(hpp, record)
            self._emit_nested_hash_specializations(hpp, record)
            # is_value_type spec MUST precede any subsequent inline method
            # body / generator struct / coro struct that template-instantiates
            # on this record. Per-record placement (same pattern as the hash
            # spec above) makes the ordering structural rather than a
            # codegen-pass invariant.
            self._emit_value_type_spec(hpp, record)
            self._emit_nested_value_type_specs(hpp, record)
            hpp.write("\n")

        # Deferred forward declarations for functions with nested types
        for func in deferred_fwd_funcs:
            self.functions.gen_function_forward_decl(hpp, func)
        if deferred_fwd_funcs:
            hpp.write("\n")

        # EnumUtil + operator<< for nested enums (must come after parent struct definitions)
        nested_enums = [e for e in module.all_enums() if "." in e.name]
        if nested_enums:
            ns = module_to_cpp_namespace(self.ctx.module_name)
            hpp.write(f"}} // namespace {ns}\n\n")
            self._gen_enum_util_decls_for(hpp, nested_enums)
            hpp.write(f"namespace {ns} {{\n\n")
            for enum in nested_enums:
                # Parser currently rejects nested @native enums, but guard
                # defensively so a future relaxation can't silently emit
                # operator<< for a native enum (would conflict with any
                # user-provided one in their C++ namespace).
                if enum.is_native:
                    continue
                self._gen_enum_operator_ostream(hpp, enum)


        # Async-method coro struct definitions FIRST (before any free coro
        # struct that might inline them as `optional<__coro_Class_method>`
        # sub-future fields -- needs the full type, not just the forward
        # decl emitted before records). Method factory definitions land
        # immediately after their struct so the inline factory body sees
        # the complete coro type.
        for record in module.all_records():
            for method in record.methods:
                if not method.is_async:
                    continue
                self.gen_async.gen_coro_struct(
                    hpp, method, record_name=record.name)
                hpp.write("\n")
                # Templates: poll() body must be inline-in-header.
                if method.type_params:
                    self.gen_async.gen_coro_poll_def(
                        hpp, method, record_name=record.name)
                    hpp.write("\n")
                    self.gen_async.gen_coro_finally_top_def(
                        hpp, method, record_name=record.name)
                    hpp.write("\n")
                struct_name = self.gen_async.gen_struct_name(method, record.name)
                cpp_record = escape_cpp_name(record.name.replace(".", "::"))
                params = self.functions.gen_params(
                    method.params, method.type_params,
                    emit_defaults=False, func=method)
                if method.params:
                    args = f"*this, {', '.join(escape_cpp_name(p) for p, _ in method.params)}"
                else:
                    args = "*this"
                const_suffix = " const" if method.is_readonly else ""
                hpp.write(f"inline {struct_name} {cpp_record}::{method.name}({params}){const_suffix} {{\n")
                hpp.write(f"    return {struct_name}({args});\n")
                hpp.write(f"}}\n\n")

        # Generator struct full definitions (after records, so struct fields
        # and inline __next__() can use fully-defined user types).
        for func in module.functions:
            if func.skip_codegen:
                continue
            if func.is_generator and not self.gen_generators.is_simple_generator(func):
                self.gen_generators.gen_generator_struct(hpp, func)
                hpp.write("\n")
            if func.is_async:
                self.gen_async.gen_coro_struct(hpp, func)
                # Templates: poll() body must be inline-in-header. Emit it
                # right after the struct so it sees fully-defined fields.
                if func.type_params:
                    self.gen_async.gen_coro_poll_def(hpp, func)
                    hpp.write("\n")
                    self.gen_async.gen_coro_finally_top_def(hpp, func)
                    hpp.write("\n")
                    self.gen_async.gen_factory(hpp, func)
                hpp.write("\n")
        # Method generator struct definitions + out-of-line factory methods
        for record in module.all_records():
            for method in record.methods:
                if method.is_generator and not self.gen_generators.is_simple_generator(method):
                    self.gen_generators.gen_generator_struct(
                        hpp, method, record_name=record.name)
                    hpp.write("\n")
                    # Inline factory method definition (now that struct is complete)
                    struct_name = self.gen_generators.gen_struct_name(method, record.name)
                    cpp_record = escape_cpp_name(record.name.replace(".", "::"))
                    params = self.functions.gen_params(
                        method.params, method, emit_defaults=False)
                    if method.params:
                        args = f"*this, {', '.join(escape_cpp_name(p) for p, _ in method.params)}"
                    else:
                        args = "*this"
                    const_suffix = " const" if method.is_readonly else ""
                    hpp.write(f"inline {struct_name} {cpp_record}::{method.name}({params}){const_suffix} {{\n")
                    hpp.write(f"    return {struct_name}({args});\n")
                    hpp.write(f"}}\n\n")

        # Trivial out-of-line method bodies (single-statement getters /
        # setters / forwarders) stay in the .hpp with ``inline`` so the
        # compiler can inline at the call site without LTO. Larger bodies
        # land in the .cpp via the matching pass at the end of `generate()`.
        # Cycle members skip the inline-in-header pass: any method body
        # that touches a cycle peer's type would need that peer's
        # complete header included from .hpp, which `<peer>_fwd.hpp`
        # doesn't carry.
        if not self.ctx.cycle_peers:
            for record in sorted_records:
                self.records.gen_record_method_defs(hpp, record, mode="def_hpp")

        # std::hash specializations are emitted per-record inline (see
        # _emit_hash_specialization), not batched here. This ensures hash
        # specs appear before any struct that uses the type as a set/dict-key.

        # Module-local type alias definitions (after record definitions
        # so member types are complete for std::variant)
        emitted_alias = False
        for name, (typ, _loc) in sorted(module.type_aliases.items()):
            if name in module.recursive_union_names:
                self._gen_recursive_union_struct(hpp, name, typ)
            else:
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
            if func.skip_codegen:
                continue
            if func.is_generator:
                if self.gen_generators.is_simple_generator(func):
                    # Simple generators: emit inline function definition in header
                    self.gen_generators.gen_simple_generator_inline(hpp, func)
                    hpp.write("\n")
                    emitted_func_decl = True
                continue  # Complex generators: factory emitted in source file
            if self.functions.gen_function_decl(hpp, func):
                emitted_func_decl = True

        if emitted_func_decl:
            hpp.write("\n")

    def _gen_reexport_using_decls(self, hpp: TextIO) -> None:
        """Emit `using` declarations for re-exported symbols (functions /
        records / enums / variables). One pass over the per-module
        attribute table buckets cells by kind; each block below emits
        its `using` / `inline auto&` shape from the bucketed entry.
        """
        table = self.analyzer.ctx.module_attributes or {}
        func_reexports: list[tuple[str, str, str, object]] = []
        record_reexports: list[tuple[str, str, str, object]] = []
        protocol_reexports: list[tuple[str, str, str, object]] = []
        enum_reexports: list[tuple[str, str, str, object]] = []
        var_reexports: list[tuple[str, str, str, object]] = []
        for name, cell in table.items():
            bd = cell.binding
            if bd.defining_module is None:
                continue  # local definition
            # The binding's `info` carries the kind-specific payload
            # (function infos / RecordInfo / enum NominalType / variable
            # TpyType). Pass it through so the kind-specific blocks
            # below can apply their filters (decorator stubs, native,
            # cpp_template, special_handling) even for direct imports
            # from implicit-stdlib roots where the consumer module's
            # registry isn't populated.
            entry = (name, bd.defining_module, bd.canonical_name, bd.info)
            if bd.kind == SymbolKind.FUNCTION:
                func_reexports.append(entry)
            elif bd.kind == SymbolKind.RECORD:
                record_reexports.append(entry)
            elif bd.kind == SymbolKind.PROTOCOL_DYNAMIC:
                # @dynamic protocols compile to abstract base classes
                # (real types), so a `using X = ns::Base;` alias works.
                # Static protocols compile to C++ concepts, which need
                # template-form alias syntax incompatible with the
                # bucketing emit path; consumers reach them via full
                # qualification instead.
                protocol_reexports.append(entry)
            elif bd.kind == SymbolKind.ENUM:
                enum_reexports.append(entry)
            elif bd.kind == SymbolKind.VARIABLE:
                var_reexports.append(entry)
        func_reexports.sort()
        record_reexports.sort()
        protocol_reexports.sort()
        enum_reexports.sort()
        var_reexports.sort()

        # Re-exported functions
        if func_reexports:
            any_written = False
            for local_name, source_module, original_name, info in func_reexports:
                # Cycle suppression: functions aren't forward-declared
                # in `<peer>_fwd.hpp`, so a `using` for a cycle peer's
                # function would reach into the peer's full header.
                # Consumers qualify through the defining module
                # directly, so this is interop-surface only.
                if source_module in self.ctx.cycle_peers:
                    continue
                # `info` is the FunctionInfo list from the binding (set
                # at install time); prefer it over registry.get_function
                # which is None for direct implicit-stdlib imports
                # bypassing the consumer's local registry.
                func_infos = info if isinstance(info, list) else (
                    self.ctx.analyzer.registry.get_function(local_name))
                # For C-linkage functions, emit an extern "C" re-declaration.
                # Using/alias re-exports don't work because the C++ name may
                # differ from the Python name (e.g., @native("SDL_GetTicks", binding="C") def get_ticks).
                # extern "C" re-decls don't reach into a sub-namespace, so
                # the sibling-cycle suppression below does not apply here.
                if func_infos and (func_infos[0].is_native_c or func_infos[0].is_extern_c):
                    self.functions.gen_extern_c_redecl(hpp, func_infos[0])
                    any_written = True
                    continue
                # Skip decorator stubs and native/template functions
                if func_infos and all(
                    fi.is_decorator_stub or fi.native_name or fi.cpp_template or fi.special_handling
                    for fi in func_infos
                ):
                    continue
                # Sibling cycle: the same shape as the cycle_peers check
                # above, induced by the parent-walk in
                # `_write_header_preamble` for descendant submodules.
                # Skipped only after the extern "C" re-decl branch since
                # that one doesn't reach into the sub-namespace.
                if self._is_descendant_submodule(source_module):
                    continue
                qualified = qualified_cpp_name(source_module, original_name)
                if local_name == original_name:
                    hpp.write(f"using {qualified};\n")
                else:
                    hpp.write(f"inline auto& {local_name} = {qualified};\n")
                any_written = True
            if any_written:
                hpp.write("\n")

        # Re-exported records / enums. Skip nested types (`.` in name): a
        # `using ::ns::Container::Inner;` at namespace scope is illegal C++
        # (using-declaration for a member at non-class scope); the inner
        # type is accessible through the outer's `using`. Also skip natives
        # / @builtin_type stubs (no namespace declaration to point at).
        # Enums also suppress cycle-peer re-exports (revisitable -- the
        # forward-declared enum in `<peer>_fwd.hpp` would in principle
        # be `using`'d safely).
        def _record_skip(info: object, name: str) -> bool:
            ri = info if isinstance(info, RecordInfo) else (
                self.analyzer.registry.get_record(name))
            return ri is not None and (ri.is_native or ri.is_keyword_stub)

        def _enum_skip(info: object, name: str) -> bool:
            et = info if info is not None else (
                self.analyzer.registry.get_enum(name))
            einfo = enum_info_of(et) if et is not None else None
            return einfo is not None and einfo.is_native

        # Mirrors `protocol_reexports_external` below; downstream record
        # references already use the fully-qualified `::tpystd::X::Foo`
        # form, so the unqualified alias would only matter inside this
        # header and fails the cyclic-include ordering check.
        record_reexports_external = [
            e for e in record_reexports
            if e[1] not in self.ctx.implicit_stdlib_modules
        ]
        # When this module is a package re-exporting records/enums from
        # a descendant submodule, the sub's header pulls in our header
        # via the parent-walk (`_write_header_preamble`), so the
        # following `using ::cur::sub::Name;` lines would otherwise
        # resolve against a not-yet-opened `cur::sub` namespace whenever
        # the sub is compiled first. Forward-declare those types in
        # their (relative) sub-namespace so the using-decls resolve at
        # any include order. Iterate the implicit-stdlib-filtered list
        # so we don't fwd-decl entries the emit-block will drop anyway.
        self._emit_sibling_submodule_fwd_decls(
            hpp, record_reexports_external, enum_reexports,
            _record_skip, _enum_skip)
        self._emit_alias_using_block(hpp, record_reexports_external, _record_skip)

        # Re-exported protocols (@dynamic generates an abstract base
        # class with the protocol's clean name; @static protocols
        # generate a C++ concept. Either way, a `using` declaration
        # makes the name accessible in the consuming module's scope so
        # references like `Box[SomeProto]` resolve at the C++ level.
        # Native marker protocols (NativeIterable, ValueType, etc.)
        # don't define a concept at the user-facing name and skip the
        # `using` -- consumers reach them through `tpy::` qualification.
        def _protocol_skip(info: object, name: str) -> bool:
            # info here is a ProtocolInfo (per symbol_binding); skip
            # native marker protocols (NativeIterable, ValueType, ...).
            # These map to C++ concepts at fixed ::tpy:: qnames and
            # don't have a TPy-generated using target.
            if isinstance(info, ProtocolInfo):
                return info.cpp_concept is not None
            return False

        # Filter out implicit-stdlib sources: the stdlib stub build
        # (each module compiled standalone for caching) doesn't have
        # later-in-chain headers visible yet when the using-declaration
        # is processed. Tpy._core importing typing.Iterable etc. is the
        # canonical case -- those names work via full qualification in
        # downstream emit paths, so skipping the `using` is safe. The
        # cross-module case that DOES need the `using` is outside-stdlib
        # (e.g. asyncio importing protocols from a sibling submodule).
        # Descendant-submodule sources are NOT dropped here unlike the
        # function/variable paths above: a static protocol compiles to
        # a C++ concept (not forward-declarable), and the parent's own
        # .cpp emits bare protocol names in function signatures
        # (e.g. `Box<AnyTask>&&` in asyncio.cpp), relying on this
        # `using` to bring the name into scope. The cycle would only
        # actually fire if the source sub-module itself triggers the
        # parent-walk back via a dotted user-module import; that
        # narrower shape isn't reproduced in the test corpus today.
        protocol_reexports_external = [
            e for e in protocol_reexports
            if e[1] not in self.ctx.implicit_stdlib_modules
        ]
        self._emit_alias_using_block(
            hpp, protocol_reexports_external, _protocol_skip,
            skip_cycle_peers=True)
        self._emit_alias_using_block(
            hpp, enum_reexports, _enum_skip, skip_cycle_peers=True)

        # Re-exported variables. Skip:
        #   - variables whose ultimate source is a native_module (no
        #     namespace path; their use sites resolve via
        #     ModuleVarInfo.native_cpp_name);
        #   - variables sourced from a cycle peer (variables aren't
        #     declared in `<peer>_fwd.hpp`, so a `using` would reach
        #     into the peer's full header);
        #   - variables sourced from a descendant submodule (the sub's
        #     parent-walk pulls us back in, so the `using` would
        #     resolve against a not-yet-opened sub namespace whenever
        #     the sub is compiled first -- mirror of the function path
        #     above). Safe to drop now that `default_to_cpp` routes
        #     default-arg references through the defining module.
        if var_reexports:
            any_written = False
            for local_name, source_module, original_name, _info in var_reexports:
                if (source_module in self.ctx.cycle_peers
                        or self._is_descendant_submodule(source_module)):
                    continue
                src_info = self.analyzer.registry.get_module(source_module)
                if src_info is not None and src_info.is_native_module:
                    continue
                qualified = qualified_cpp_name(source_module, original_name)
                hpp.write(f"inline auto& {local_name} = {qualified};\n")
                any_written = True
            if any_written:
                hpp.write("\n")

    def _is_descendant_submodule(self, source_module: str) -> bool:
        """True when `source_module` is a strict descendant of the
        current (package) module. Used to identify re-exports that
        induce a parent<->sub header cycle: the sub's parent-walk
        emits our header first, so any `using ::cur::sub::Name;`
        we emit inside our namespace runs while `cur::sub` is mid-
        parsing and hasn't opened yet.
        """
        cur = self.ctx.module_name
        return source_module.startswith(cur + ".")

    def _emit_sibling_submodule_fwd_decls(
        self, hpp: TextIO,
        record_entries: list[tuple[str, str, str, object]],
        enum_entries: list[tuple[str, str, str, object]],
        record_skip: Callable[[object, str], bool],
        enum_skip: Callable[[object, str], bool],
    ) -> None:
        """Emit forward declarations of records/enums re-exported from
        descendant submodules, grouped by relative sub-namespace path.
        Lives inside our own namespace block, so relative names like
        `namespace sub { struct X; }` resolve to `cur::sub::X` and
        satisfy the using-decls emitted just after this block. Skip
        filters mirror the using-decl emitter so we don't fwd-decl
        natives, keyword stubs, or nested-name types that the
        using-decl emitter would also reject.
        """
        cur = self.ctx.module_name
        by_subpath: dict[str, list[str]] = {}

        def add(entry, kind: str, skip_fn: Callable[[object, str], bool]) -> None:
            local_name, source_module, original_name, info = entry
            if not self._is_descendant_submodule(source_module):
                return
            if "." in local_name or "." in original_name:
                return
            if skip_fn(info, original_name):
                return
            subpath = source_module[len(cur) + 1:].replace(".", "::")
            if kind == "record":
                ri = info if isinstance(info, RecordInfo) else (
                    self.analyzer.registry.get_record(original_name))
                if ri is not None and ri.type_params:
                    # Class template: mirror the full template header from
                    # `gen_record_template_header` so the forward decl
                    # agrees with the sub-header's declaration.
                    header = self.protocols.gen_record_template_header(
                        ri.type_params, ri.type_param_bounds,
                        ri.type_param_kinds)
                    by_subpath.setdefault(subpath, []).append(
                        f"{header} struct {original_name};")
                else:
                    by_subpath.setdefault(subpath, []).append(
                        f"struct {original_name};")
            else:
                # `enum class Name : underlying;` -- underlying must
                # match the definition. Resolve via the registered
                # NominalType (same source the `<peer>_fwd.hpp` path
                # uses in `generate_fwd_header`).
                et = info if info is not None else (
                    self.analyzer.registry.get_enum(original_name))
                einfo = enum_info_of(et) if et is not None else None
                underlying = (einfo.underlying_type.to_cpp()
                              if einfo is not None else "int32_t")
                by_subpath.setdefault(subpath, []).append(
                    f"enum class {original_name} : {underlying};")

        for entry in record_entries:
            add(entry, "record", record_skip)
        for entry in enum_entries:
            add(entry, "enum", enum_skip)

        if not by_subpath:
            return
        for subpath in sorted(by_subpath):
            decls = " ".join(by_subpath[subpath])
            hpp.write(f"namespace {subpath} {{ {decls} }}\n")
        hpp.write("\n")

    def _emit_alias_using_block(
        self, hpp: TextIO,
        entries: list[tuple[str, str, str, object]],
        skip: Callable[[object, str], bool],
        *, skip_cycle_peers: bool = False,
    ) -> None:
        """Emit `using ::ns::Foo;` (or `using Local = ::ns::Foo;`) for
        each entry in `entries`, skipping nested types and entries the
        predicate rejects. Entries are `(local_name, source_module,
        original_name, info)` tuples sourced from the bucketing pass in
        `_gen_reexport_using_decls`. Trailing blank line if anything
        was written.
        """
        if not entries:
            return
        any_written = False
        for local_name, source_module, original_name, info in entries:
            if skip_cycle_peers and source_module in self.ctx.cycle_peers:
                continue
            if "." in local_name or "." in original_name:
                continue
            if skip(info, original_name):
                continue
            qualified = qualified_cpp_name(source_module, original_name)
            if local_name == original_name:
                hpp.write(f"using {qualified};\n")
            else:
                hpp.write(f"using {local_name} = {qualified};\n")
            any_written = True
        if any_written:
            hpp.write("\n")

    def _gen_recursive_union_struct(self, out: TextIO, name: str, typ: UnionType) -> None:
        """Generate a wrapper struct for a recursive union type alias.

        Instead of `using JsonValue = std::variant<...>`, emits:
          struct JsonValue {
              using variant_type = std::variant<...>;
              variant_type value;
              JsonValue() = default;
              template<typename T> requires ... JsonValue(T&& v) : data(...) {}
              bool operator==(const JsonValue&) const = default;
          };
        """
        cpp_members = [
            "std::monostate" if is_void_like_type(m) else self.types.type_to_cpp(m)
            for m in typ.members
        ]
        variant_type = f"std::variant<{', '.join(cpp_members)}>"
        out.write(f"struct {name} {{\n")
        out.write(f"    using variant_type = {variant_type};\n")
        out.write(f"    variant_type value;\n\n")
        out.write(f"    {name}() = default;\n")
        out.write(f"    template<typename T>\n")
        out.write(f"        requires std::constructible_from<variant_type, T&&>\n")
        out.write(f"    {name}(T&& v) : value(std::forward<T>(v)) {{}}\n\n")
        out.write(f"    bool operator==(const {name}&) const = default;\n\n")
        out.write(f"    friend std::ostream& operator<<(std::ostream& os, const {name}& v) {{\n")
        out.write(f"        ::tpy::detail::print_element(os, v.value);\n")
        out.write(f"        return os;\n")
        out.write(f"    }}\n")
        out.write(f"}};\n")

    def _gen_enum_decl(self, out: TextIO, enum) -> None:
        """Generate C++ enum class declaration (no helpers)."""
        enum_type = self.ctx.analyzer.registry.get_enum(enum.name)
        if not enum_type:
            return
        underlying = enum_info_of(enum_type).underlying_type.to_cpp()

        out.write(f"enum class {enum.name} : {underlying} {{\n")
        for member_name, value, _ in enum.members:
            out.write(f"    {member_name} = {value},\n")
        out.write("};\n\n")

    def _gen_dynamic_adapter_specs(self, out: TextIO, module: TpyModule,
                                    dynamic_protocols: list) -> None:
        """Generate ::tpy::Adapter/RefAdapter partial specializations (global scope)."""
        ns = module_to_cpp_namespace(self.ctx.module_name)
        # User-namespace type names that must be qualified inside `tpy::`
        # adapter overrides; see ProtocolGenerator._qualify_user_types.
        user_type_names = {r.name for r in module.records} | {p.name for p in module.protocols}
        for protocol in dynamic_protocols:
            self.protocols.gen_dynamic_adapter_specs(out, protocol, ns, user_type_names)
            out.write("\n")

    def _gen_enum_util_decls_for(self, out: TextIO, enums: list) -> None:
        """Generate ::tpy::EnumUtil<E> specialization declarations for given enums."""
        from .context import enum_cpp_name
        cur_module = self.ctx.module_name
        for enum in enums:
            enum_type = self.ctx.analyzer.registry.get_enum(enum.name)
            if not enum_type:
                continue
            underlying = enum_info_of(enum_type).underlying_type.to_cpp()
            qualified = enum_cpp_name(enum_type, cur_module, absolute=True)
            short_name = bare_name(enum.name)
            member_count = len(enum.members)
            out.write(f"template<>\n")
            out.write(f"struct tpy::EnumUtil<{qualified}> {{\n")
            out.write(f"    static constexpr std::string_view type_name = \"{short_name}\";\n")
            out.write(f"    static std::string_view name({qualified} e);\n")
            out.write(f"    static const std::array<{qualified}, {member_count}> members;\n")
            out.write(f"    static {qualified} from_value({underlying} v);\n")
            out.write(f"    static {qualified} from_name(std::string_view s);\n")
            out.write(f"    static std::optional<{qualified}> try_parse(std::string_view s);\n")
            out.write(f"}};\n\n")

    def _gen_enum_operator_ostream(self, out: TextIO, enum) -> None:
        """Generate inline operator<< inside user namespace."""
        cpp_name = enum.name.replace(".", "::")
        # Use short name for repr to match CPython (Kind.TEXT, not Message.Kind.TEXT)
        short_name = bare_name(enum.name)
        out.write(f"inline std::ostream& operator<<(std::ostream& __os, {cpp_name} __e) {{\n")
        out.write(f"    return __os << \"{short_name}.\" << ::tpy::EnumUtil<{cpp_name}>::name(__e);\n")
        out.write(f"}}\n\n")

    def _gen_enum_source_defs(self, out: TextIO, module: TpyModule) -> None:
        """Generate ::tpy::EnumUtil<E> member definitions in namespace tpy."""
        from .context import enum_cpp_name
        cur_module = self.ctx.module_name
        out.write("namespace tpy {\n\n")
        for enum in module.all_enums():
            enum_type = self.ctx.analyzer.registry.get_enum(enum.name)
            if not enum_type:
                continue
            einfo = enum_info_of(enum_type)
            underlying = einfo.underlying_type.to_cpp()
            qualified = enum_cpp_name(enum_type, cur_module, absolute=True)
            short_name = bare_name(enum.name)
            member_count = len(enum.members)
            # cpp_of(python_name) -> C++ enumerator name (Python name if no
            # native_member() override). Used at every site that emits a
            # `qualified::<member>` C++ symbol reference. Sites that emit
            # the Python-side string (name() return value, try_parse key)
            # keep the Python name. Built once per enum to avoid O(N**2)
            # over the per-member emission loops.
            _cpp_map = einfo.cpp_member_name_map
            def cpp_of(n: str, _m: dict[str, str] = _cpp_map) -> str:
                return _m.get(n, n)

            # @native enum + explicit values: pin each declared value to the
            # C++ side at compile time. Lives in the .cpp (not the .hpp) so it
            # doesn't bloat every translation unit that includes the header --
            # the assertion still fires at the same point, when this .cpp
            # compiles. auto()/native_member() opt out -- the user did not
            # declare a value.
            if enum.is_native and enum.has_explicit_values:
                for member_name, value, _ in enum.members:
                    out.write(
                        f"static_assert(static_cast<{underlying}>({qualified}::{cpp_of(member_name)}) == {value}, "
                        f"\"TPy-declared value for {short_name}.{member_name} does not match C++ side\");\n"
                    )
                out.write("\n")

            # name()
            out.write(f"std::string_view EnumUtil<{qualified}>::name({qualified} __e) {{\n")
            out.write(f"    switch (__e) {{\n")
            for member_name, _, _ in enum.members:
                out.write(
                    f"        case {qualified}::{cpp_of(member_name)}: "
                    f"return \"{member_name}\";\n"
                )
            # Internal invariant: TPy enum values are always one of the declared
            # cases, so the default is unreachable from well-typed code. Stays
            # panic (not raise<ValueError>) -- the from_value path below already
            # raises ValueError for user-supplied invalid values.
            out.write(f"        default: tpy_panic(\"invalid enum value\");\n")
            out.write(f"    }}\n")
            out.write(f"}}\n\n")

            # members
            out.write(f"const std::array<{qualified}, {member_count}>\n")
            out.write(f"EnumUtil<{qualified}>::members = {{\n")
            for member_name, _, _ in enum.members:
                out.write(f"    {qualified}::{cpp_of(member_name)},\n")
            out.write(f"}};\n\n")

            # from_value(). For @native enums the C++ side is the source
            # of truth for member values, so cases key off the C++
            # enumerator's actual value (not the TPy-declared int, which
            # may be `auto()`-assigned and meaningless for the binding).
            out.write(f"{qualified} EnumUtil<{qualified}>::from_value({underlying} __v) {{\n")
            out.write(f"    switch (__v) {{\n")
            for member_name, value, _ in enum.members:
                if enum.is_native:
                    out.write(
                        f"        case static_cast<{underlying}>({qualified}::{cpp_of(member_name)}): "
                        f"return {qualified}::{cpp_of(member_name)};\n"
                    )
                else:
                    out.write(f"        case {value}: return {qualified}::{member_name};\n")
            out.write(f"        default: raise<ValueError>(\"{{}} is not a valid {enum.name}\", __v);\n")
            out.write(f"    }}\n")
            out.write(f"}}\n\n")

            # try_parse(): keys are Python-side names (user-facing), returns
            # are C++ enumerators (which may differ for @native enums).
            out.write(f"std::optional<{qualified}> EnumUtil<{qualified}>::try_parse(std::string_view __name) {{\n")
            member_names = [name for name, _, _ in enum.members]
            if len(member_names) >= STRING_SWITCH_THRESHOLD:
                self._gen_enum_try_parse_switch(out, qualified, member_names, cpp_of)
            else:
                for member_name in member_names:
                    out.write(
                        f"    if (__name == \"{member_name}\") "
                        f"return {qualified}::{cpp_of(member_name)};\n"
                    )
            out.write(f"    return std::nullopt;\n")
            out.write(f"}}\n\n")

            # from_name() delegates to try_parse()
            out.write(f"{qualified} EnumUtil<{qualified}>::from_name(std::string_view __name) {{\n")
            out.write(f"    auto __result = try_parse(__name);\n")
            out.write(f"    if (!__result.has_value()) raise<KeyError>(\"{{}}\", __name);\n")
            out.write(f"    return *__result;\n")
            out.write(f"}}\n\n")

        out.write("} // namespace tpy\n\n")

    @staticmethod
    def _gen_enum_try_parse_switch(
        out: TextIO, qualified: str,
        member_names: list[str],
        cpp_of: Callable[[str], str],
    ) -> None:
        """Generate switch-based try_parse for enum with many members.

        `cpp_of` maps Python-side member names to C++ enumerator names
        (identity for tpy-defined enums; honors native_member() overrides
        for @native enums). Members are keyed by Python name (user-facing
        string), returned as their C++ enumerator.
        """
        kind, param, _buckets = find_best_discriminator(member_names)

        # Build bucket -> [member_name] mapping
        buckets: dict[int, list[str]] = {}
        for name in member_names:
            key = len(name) if kind == "length" else ord(name[param])
            buckets.setdefault(key, []).append(name)

        if kind == "length":
            out.write(f"    switch (__name.size()) {{\n")
            case_indent = "    "
            body_indent = "        "
        else:
            out.write(f"    if (__name.size() >= {param + 1}) {{\n")
            out.write(f"        switch (static_cast<unsigned char>(__name[{param}])) {{\n")
            case_indent = "        "
            body_indent = "            "

        for disc_value in sorted(buckets.keys()):
            names = buckets[disc_value]
            if kind == "char_at":
                ch = chr(disc_value)
                out.write(f"{case_indent}case '{escape_cpp_char(ch)}': {{\n")
            else:
                out.write(f"{case_indent}case {disc_value}: {{\n")
            for name in names:
                out.write(
                    f"{body_indent}if (__name == \"{name}\") "
                    f"return {qualified}::{cpp_of(name)};\n"
                )
            out.write(f"{body_indent}break;\n")
            out.write(f"{case_indent}}}\n")

        out.write(f"{case_indent}}}\n")
        if kind == "char_at":
            out.write(f"    }}\n")

    def _module_to_include_path(self, module_name: str, *,
                                 prefer_fwd: bool = False) -> str:
        """Convert dotted module name to include path.

        When `prefer_fwd` is True AND `module_name` is a cycle peer of
        the current module, returns `<mod>_fwd.hpp` instead of
        `<mod>.hpp`. The fwd header carries forward declarations of
        the peer's records / enums / @dynamic protocols so the cyclic
        include resolves without requiring complete types.
        """
        from .context import module_to_include_path
        path = module_to_include_path(module_name)
        if prefer_fwd and module_name in self.ctx.cycle_peers:
            if path.endswith(".hpp"):
                return path[:-len(".hpp")] + "_fwd.hpp"
        return path

    def _write_header_preamble(self, out: TextIO,
                               native_funcs: list[TpyFunction] | None = None,
                               native_globals: list[TpyVarDecl] | None = None,
                               directives: ModuleDirectives | None = None) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write("#pragma once\n\n")
        out.write('#include <tpy/tpy.hpp>\n')
        included: set[str] = set()
        if directives and directives.includes:
            for include_path, platform in directives.includes:
                if not _platform_matches(platform):
                    continue
                if include_path.startswith('<') and include_path.endswith('>'):
                    out.write(f'#include {include_path}\n')
                else:
                    out.write(f'#include "{include_path}"\n')
                included.add(include_path)
            out.write('\n')
        # Skip implicit stdlib peers we don't actually depend on -- auto-
        # including unrelated peers creates cycles when two stdlib modules
        # cross-reference each other. Strip self-parent prefixes because
        # qname-derived reach collapses `tpy.Foo` to "tpy" even for types
        # defined in this module.
        own_deps = set(self.ctx.user_module_imports) | set(self.analyzer.ctx.reached)
        own_parents = {
            ".".join(self.ctx.module_name.split(".")[:i + 1])
            for i in range(self.ctx.module_name.count("."))
        }
        own_parents.add(self.ctx.module_name)
        own_deps -= own_parents
        # Closed-under-prefixes set of deps: dep `tpy._core._types` makes
        # peer `tpy._core` match (peer is an ancestor of a dep). Direction
        # 1 (peer descends from a dep) still keys on `own_deps` itself --
        # NOT this closure -- because parent-stripping above removed
        # `own_parents` from `own_deps` to suppress self-refs, and a
        # full prefix-closure would re-add them.
        dep_prefix_closure = set(own_deps)
        for dep in own_deps:
            dep_parts = dep.split(".")
            for i in range(1, len(dep_parts)):
                dep_prefix_closure.add(".".join(dep_parts[:i]))

        def _dep_match(peer: str) -> bool:
            if peer in dep_prefix_closure:
                return True
            peer_parts = peer.split(".")
            for i in range(1, len(peer_parts)):
                if ".".join(peer_parts[:i]) in own_deps:
                    return True
            return False

        for implicit_mod in sorted(self.ctx.implicit_stdlib_modules):
            if implicit_mod == self.ctx.module_name:
                continue
            mod_info = self.analyzer.registry.get_module(implicit_mod)
            if mod_info and not mod_info.generates_header:
                continue
            if not _dep_match(implicit_mod):
                continue
            if implicit_mod in self.ctx.all_user_modules:
                include_path = self._module_to_include_path(implicit_mod)
                out.write(f'#include "{include_path}"\n')
                included.add(implicit_mod)
        # Include user module headers (including parent packages for dotted imports).
        # Driven by the union of user_module_imports (today's literal-import set)
        # and module_reached (types referenced through field/method chains).
        # For native modules we recurse through their own reach so that
        # # tpy: include() directives flow across native-to-native chains --
        # natives have no .hpp to chain through, so the consumer must emit
        # them all directly.
        chase_visited: set[str] = set()

        def emit_native_includes(info) -> None:
            for inc, platform in info.includes:
                if not _platform_matches(platform):
                    continue
                if inc in included:
                    continue
                if inc.startswith('<') and inc.endswith('>'):
                    out.write(f'#include {inc}\n')
                else:
                    out.write(f'#include "{inc}"\n')
                included.add(inc)

        def emit_non_native(mod_name: str) -> None:
            if mod_name == self.ctx.module_name or mod_name in included:
                return
            # Parent package headers first, for dotted modules
            parts = mod_name.split('.')
            for i in range(1, len(parts)):
                parent_pkg = '.'.join(parts[:i])
                if parent_pkg == self.ctx.module_name:
                    continue
                if parent_pkg in self.ctx.all_user_modules and parent_pkg not in included:
                    parent_info = self.analyzer.registry.get_module(parent_pkg)
                    if parent_info is not None and not parent_info.generates_header:
                        # Native-module package init has no .hpp; surface its
                        # raw includes (if any) and mark as visited so we don't
                        # try again for sibling submodules.
                        emit_native_includes(parent_info)
                        included.add(parent_pkg)
                        continue
                    out.write(f'#include "{self._module_to_include_path(parent_pkg, prefer_fwd=True)}"\n')
                    included.add(parent_pkg)
            out.write(f'#include "{self._module_to_include_path(mod_name, prefer_fwd=True)}"\n')
            included.add(mod_name)

        def visit(mod_name: str) -> None:
            if mod_name in chase_visited or mod_name == self.ctx.module_name:
                return
            chase_visited.add(mod_name)
            mod_info = self.analyzer.registry.get_module(mod_name)
            if mod_info is None:
                return
            if mod_info.is_builtin:
                return
            if not mod_info.generates_header:
                # Skip implicit stdlib native shims (decorator stubs, no C++)
                if mod_name in self.ctx.implicit_stdlib_modules:
                    return
                emit_native_includes(mod_info)
                # Natives have no .hpp to chain through, so the consumer must
                # see every reach the native exposes -- recurse explicitly.
                for next_mod in sorted(mod_info.reached):
                    visit(next_mod)
                return
            # Non-native: emit the hpp; preprocessor handles its transitive deps.
            emit_non_native(mod_name)

        seed = set(self.ctx.user_module_imports)
        seed.update(self.analyzer.ctx.reached)
        for user_mod in sorted(seed):
            visit(user_mod)
        # Include macro dependency module headers (from MACRO_DEPS)
        for dep_mod in sorted(self.ctx.macro_dep_modules):
            if dep_mod in included or dep_mod == self.ctx.module_name:
                continue
            module_info = self.analyzer.registry.get_module(dep_mod)
            if module_info and (module_info.is_builtin or not module_info.generates_header):
                continue
            if dep_mod in self.ctx.all_user_modules:
                include_path = self._module_to_include_path(dep_mod)
                out.write(f'#include "{include_path}"\n')
                included.add(dep_mod)
        out.write("\n")

        # Native global extern declarations go before the tpyapp namespace
        if native_globals:
            for stmt in native_globals:
                cpp_name = stmt.native_name or stmt.name
                var_type = resolve_stmt_type_cascade(stmt, self.analyzer, self.types)
                if stmt.linkage == VarLinkage.NATIVE_C_ARRAY:
                    # C array global: Ptr[T] -> extern "C" T name[];
                    # The incomplete array type decays to T* when used.
                    if isinstance(var_type, PtrType):
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

    def generate_fwd_header(self, module: TpyModule, module_name: str) -> str:
        """Emit `<mod>_fwd.hpp` -- forward declarations of every record /
        enum / @dynamic protocol defined in this module's namespace,
        plus the namespace skeleton itself. Cycle peers in the same SCC
        include this header in place of `<mod>.hpp` to break the
        cyclic complete-type include while keeping access to the type
        names.

        Carries declarations only (no method bodies, no field
        layouts), so positions that need complete-type info (by-value
        fields, container elements, concrete inheritance, ...) are
        rejected up-front by `_check_workspace_completeness_cycles`.
        """
        from .context import module_to_cpp_namespace
        ns = module_to_cpp_namespace(module_name)
        out_buf = io.StringIO()
        out_buf.write("// Generated by TurboPython Compiler -- forward declarations for cycle peers\n")
        out_buf.write("#pragma once\n\n")
        out_buf.write(f"namespace {ns} {{\n\n")
        for record in module.all_records():
            cpp_name = record.name.replace(".", "::")
            out_buf.write(f"struct {cpp_name};\n")
        for enum in module.all_enums():
            # @native enums: skip the forward decl. The user's
            # `# tpy: include(...)` provides the type; re-declaring with
            # the TPy-recorded underlying type would risk ODR mismatch.
            if enum.is_native:
                continue
            cpp_name = enum.name.replace(".", "::")
            # `enum class Name : underlying;` -- the underlying type
            # in the forward declaration MUST agree with the
            # definition's. Pull it from the EnumInfo attached to
            # the registered NominalType so both this fwd header
            # and the full header in `_gen_enum_decl` use the same
            # type and the C++ compiler accepts the redeclaration.
            enum_type = self.ctx.analyzer.registry.get_enum(enum.name)
            if enum_type is None:
                # Skeleton-pre-pop NominalType lacks an EnumInfo until
                # `register_enum` runs; default to `int32_t` (matches
                # `register_enum`'s default). The peer's full header
                # will redeclare with the same underlying type.
                underlying = "int32_t"
            else:
                underlying = enum_info_of(enum_type).underlying_type.to_cpp()
            out_buf.write(f"enum class {cpp_name} : {underlying};\n")
        for protocol in module.protocols:
            # @dynamic protocols emit a `struct {Name}` base class that
            # IS forward-declarable. Static (structural) protocols emit
            # only a C++20 concept, which is NOT forward-declarable;
            # cycle peers using a static protocol's name as a template
            # constraint must include the full peer header (a complete-
            # type position for the completeness-graph reject gate).
            if protocol.is_dynamic:
                ProtocolGenerator.emit_dynamic_base_forward_decl(out_buf, protocol)
        out_buf.write(f"\n}} // namespace {ns}\n")
        return out_buf.getvalue()

    def _write_source_preamble(self, out: TextIO, module: TpyModule) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        # Use full include path from include root (consistent with header includes)
        include_path = self._module_to_include_path(self.ctx.module_name)
        out.write(f'#include "{include_path}"\n')
        # Cycle peers: the .hpp included <peer>_fwd.hpp for each, but
        # the .cpp needs the FULL <peer>.hpp so function bodies can
        # see complete types of cycle peers' records.
        for peer in sorted(self.ctx.cycle_peers):
            if peer == self.ctx.module_name:
                continue
            out.write(f'#include "{self._module_to_include_path(peer)}"\n')
        out.write("\n")
        # EnumUtil definitions go before user namespace (they live in namespace tpy)
        if module.all_enums():
            self._gen_enum_source_defs(out, module)
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"namespace {ns} {{\n\n")

    def _write_header_epilogue(self, out: TextIO) -> None:
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n")
