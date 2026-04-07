"""
TurboPython Record Code Generation

Generates C++ structs from TurboPython records.
"""

from __future__ import annotations
from collections import defaultdict
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OptionalType, OwnType, ReadonlyType,
    TypeParamRef, TypeParamKind, RecordInfo, TupleType, UnionType,
    ArrayType, SpanType, SpanIterType, unwrap_readonly, unwrap_optional_own,
    get_covariant_params, FixedIntType, BigIntType, EnumType, PtrType,
)
from ..parse import (
    TpyRecord, TpyFunction, TpyStmt, TpyExprStmt, TpyAssign,
    TpyMethodCall, TpyFieldAccess, TpyName, TpyCoerce, TpyNestedDef, is_super_del_call,
)
from ..namespace import Namespace

from .context import INDENT, DUNDER_TO_BINARY_OP, CodeGenError, escape_cpp_name
from .functions import factory_default_to_cpp

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .expressions import ExpressionGenerator
    from .functions import FunctionGenerator
    from .protocols import ProtocolGenerator


class RecordGenerator:
    """Generates C++ structs from TurboPython records."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        protocols: ProtocolGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.protocols = protocols
        # Will be set after dependencies are created
        self.expressions: ExpressionGenerator | None = None
        self.functions: FunctionGenerator | None = None
        self.gen_generators = None  # Set by CodeGenerator after init

    def set_dependencies(self, expressions: ExpressionGenerator, functions: FunctionGenerator):
        """Set expression and function generators (to break circular dependency)."""
        self.expressions = expressions
        self.functions = functions

    def sort_records_by_inheritance(self, records: list[TpyRecord]) -> list[TpyRecord]:
        """Sort records so parent classes come before children.

        Uses topological sort based on inheritance relationships.
        Native records are excluded (no C++ struct generation needed).
        """
        # Filter out native records -- they don't generate C++ structs
        records = [r for r in records if not self._is_native(r)]
        record_by_name = {r.name: r for r in records}

        # Build dependency graph
        # Only consider user-defined parents (NamedType records), not builtin types
        dependencies: dict[str, set[str]] = {r.name: set() for r in records}
        for record in records:
            record_info = self.ctx.analyzer.registry.get_record(record.name)
            if (record_info and record_info.parent and
                isinstance(record_info.parent, NamedType) and record_info.parent.is_user_record and
                record_info.parent.name in record_by_name):
                dependencies[record.name].add(record_info.parent.name)

        # Topological sort (Kahn's algorithm)
        result = []
        no_deps = [name for name, deps in dependencies.items() if not deps]

        while no_deps:
            name = no_deps.pop(0)
            result.append(record_by_name[name])

            # Remove this record from all dependents
            for dep_name, deps in dependencies.items():
                if name in deps:
                    deps.remove(name)
                    if not deps and dep_name not in [r.name for r in result]:
                        no_deps.append(dep_name)

        # If any records are left (circular dependency), add them at the end
        for record in records:
            if record not in result:
                result.append(record)

        return result

    def _is_field_type_nocopy(self, typ: TpyType) -> bool:
        """Check if a field type is nocopy (for comment generation)."""
        if isinstance(typ, (ReadonlyType, OwnType)):
            return self._is_field_type_nocopy(typ.wrapped)
        if isinstance(typ, OptionalType):
            return self._is_field_type_nocopy(typ.inner)
        record = self.ctx.analyzer.registry.get_record_for_type(typ)
        if record is not None and record.is_nocopy:
            return True
        if isinstance(typ, NamedType) and typ.type_args:
            if record is not None and record.has_copy:
                return False
            return any(
                isinstance(a, TpyType) and self._is_field_type_nocopy(a)
                for a in typ.type_args
            )
        return False

    def _find_nocopy_field(self, record_info: RecordInfo) -> str | None:
        """Find the name of the first nocopy field, for diagnostic comments."""
        for f in record_info.fields:
            if self._is_field_type_nocopy(f.type):
                return f.name
        if record_info.parent is not None:
            parent_rec = self.ctx.analyzer.registry.get_record_for_type(record_info.parent)
            if parent_rec is not None and parent_rec.is_nocopy:
                return None  # parent nocopy, not a field
        return None

    def _is_native(self, record_or_name) -> bool:
        """Check if a record/type is a native import (no C++ generation needed)."""
        name = record_or_name.name if hasattr(record_or_name, 'name') else record_or_name
        record_info = self.ctx.analyzer.registry.get_record(name)
        return record_info is not None and record_info.is_native

    def gen_record_decl(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a struct declaration for a record."""
        # Native records don't generate C++ structs -- they're defined in external headers
        if self._is_native(record):
            return

        # Get record info for inheritance information
        record_info = self.ctx.analyzer.registry.get_record(record.name)

        # Emit class header as source comments (preceding comments + class line)
        if record.loc:
            self.ctx.emit_preceding_comments(out, record.loc)
            self.ctx.emit_source_comment(out, record.loc)

        # Generate template prefix for generic records
        if record.type_params:
            template_header = self.protocols.gen_record_template_header(
                record.type_params, record.type_param_bounds, record.type_param_kinds
            )
            out.write(f"{template_header}\n")

        # Generate struct with optional inheritance
        # Collect base classes: user-defined parent + @dynamic protocol bases
        bases = []
        if record_info and record_info.parent:
            bases.append(record_info.parent.to_cpp())
        if record_info:
            for proto in record_info.implemented_protocols:
                proto_info = self.ctx.analyzer.registry.get_protocol(proto.name)
                if proto_info and proto_info.is_dynamic:
                    bases.append(self.protocols.get_dynamic_base_name(proto.name))
        cpp_rec_name = escape_cpp_name(record.name)
        if bases:
            out.write(f"struct {cpp_rec_name} : {', '.join(bases)} {{\n")
        else:
            out.write(f"struct {cpp_rec_name} {{\n")

        # Fields
        for fld in record.fields:
            self.ctx.emit_preceding_comments(out, fld.loc, indent=INDENT)
            self.ctx.emit_source_comment(out, fld.loc, indent=INDENT)
            cpp_type = self.types.type_to_cpp(fld.type)
            default = ""
            if fld.default_value is not None:
                default = f" = {fld.default_value}"
            elif fld.is_factory_default:
                default = f" = {factory_default_to_cpp(fld.type)}"
            out.write(f"{INDENT}{cpp_type} {escape_cpp_name(fld.name)}{default};\n")

        # Drop flag for classes with __del__ -- prevents double-drop after move.
        # Only emit on the root class; children inherit the parent's flag so that
        # all destructors in the chain check the same field.
        if record.del_method is not None and not self.ctx.any_ancestor_has_del(record.name):
            out.write(f"{INDENT}bool __tpy_owned_ = true;\n")

        out.write("\n")

        # Determine constructor generation strategy
        if record.init_method:
            has_params = bool(record.init_method.params)
            saved_func_params = self.ctx.current_func_params
            saved_in_method = self.ctx.in_method
            self.ctx.current_func_params = {
                pname: ptype for pname, ptype in record.init_method.params}
            self.ctx.in_method = True
            base_init = self._extract_base_init(record.init_method, record)
            inits = self._extract_field_inits(record.init_method, record)
            self.ctx.current_func_params = saved_func_params
            self.ctx.in_method = saved_in_method
            non_init_stmts = self._get_non_init_stmts(record.init_method, record)

            self.ctx.emit_preceding_comments(out, record.init_method.loc, indent=INDENT)
            self.ctx.emit_source_comment(out, record.init_method.loc, indent=INDENT)
            if has_params:
                init_defaults = record.init_method.defaults if record.init_method.defaults else None
                has_required_params = not init_defaults or any(d is None for d in init_defaults)

                # Generate parameterized constructor from __init__
                proto_params = self.functions.protocols.get_all_protocol_params(
                    record.init_method.params)
                has_dynamic = self.functions._has_dynamic_protocol_params(
                    record.init_method.params)

                # Default constructor: when all params have defaults and every
                # protocol param is optional, delegate to the template ctor with
                # nullptr so the __init__ body executes (else branch runs via
                # if constexpr). Otherwise use = default.
                all_protocols_optional = (
                    proto_params
                    and not has_required_params
                    and all(p.has_none for p in proto_params))
                if has_required_params or proto_params:
                    if all_protocols_optional:
                        out.write(f"{INDENT}{cpp_rec_name}() : {cpp_rec_name}(static_cast<std::nullptr_t*>(nullptr)) {{}}\n")
                    elif self._all_fields_default_constructible(record):
                        out.write(f"{INDENT}{cpp_rec_name}() = default;\n")
                if proto_params or has_dynamic:
                    cpp_params = self.functions.gen_params_with_protocols(
                        record.init_method.params,
                        record.init_method.type_params,
                        const_params=True,
                        defaults=init_defaults,
                        emit_defaults=True,
                    )
                    if proto_params:
                        template_header = self.functions.protocols.gen_combined_template_header(
                            record.init_method.type_params or [], proto_params,
                            record.type_param_bounds or None,
                        )
                        out.write(f"{INDENT}{template_header}")
                else:
                    cpp_params = self.functions.gen_params(
                        record.init_method.params,
                        record.init_method.type_params,
                        const_params=True,
                        defaults=init_defaults,
                        emit_defaults=True,
                        class_type_params=set(record.type_params) if record.type_params else None,
                    )
                out.write(f"{INDENT}explicit {cpp_rec_name}({cpp_params})")
            else:
                # No params: generate default constructor with body
                out.write(f"{INDENT}{cpp_rec_name}()")

            # Build member init list: base init (if any) + field inits
            all_inits = []
            if base_init:
                all_inits.append(base_init)
            all_inits.extend(f"{escape_cpp_name(name)}({val})" for name, val in inits)
            if all_inits:
                out.write(" : ")
                out.write(", ".join(all_inits))
            if non_init_stmts:
                out.write(" {\n")
                local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
                local_ns.bind_variable("self", NamedType(record.name))
                for pname, ptype in record.init_method.params:
                    local_ns.bind_variable(pname, ptype)
                self.functions.gen_body(out, non_init_stmts, record.init_method.params,
                                         record.init_method.return_type, record.init_method,
                                         local_ns, indent_level=2, is_method=True,
                                         record_type_param_bounds=record.type_param_bounds or None)
                out.write(f"{INDENT}}}\n")
            else:
                out.write(" {}\n")
        else:
            # No __init__: for plain records, omit constructor declaration so the
            # struct stays a C++ aggregate (supports both Type() and Type(a,b,c)).
            # Records with __del__/@nocopy get user-declared copy/move ops which
            # suppress the implicit default ctor, so they still need = default.
            if record_info and (record_info.is_nocopy or record_info.has_del or record_info.has_copy):
                out.write(f"{INDENT}{cpp_rec_name}() = default;\n")

        # Copy/move ops for @nocopy, __del__, or __copy__ classes.
        if record_info and record_info.has_copy:
            n = cpp_rec_name
            out.write(f"{INDENT}// copyable via __copy__\n")
            out.write(f"{INDENT}{n}(const {n}& other) : {n}(other.__copy__()) {{}}\n")
            out.write(f"{INDENT}{n}& operator=(const {n}& other) {{\n")
            out.write(f"{INDENT}{INDENT}if (this != &other) {{ *this = other.__copy__(); }}\n")
            out.write(f"{INDENT}{INDENT}return *this;\n")
            out.write(f"{INDENT}}}\n")
            if not record_info.has_del:
                out.write(f"{INDENT}{n}({n}&&) = default;\n")
                out.write(f"{INDENT}{n}& operator=({n}&&) = default;\n")
        elif record_info and (record_info.is_nocopy or record_info.has_del):
            if record_info.is_nocopy:
                out.write(f"{INDENT}// non-copyable")
                nocopy_field = self._find_nocopy_field(record_info)
                if record.is_nocopy:
                    out.write(" (@nocopy)")
                elif nocopy_field:
                    out.write(f" (field '{nocopy_field}')")
                out.write("\n")
            out.write(f"{INDENT}{cpp_rec_name}(const {cpp_rec_name}&) = delete;\n")
            out.write(f"{INDENT}{cpp_rec_name}& operator=(const {cpp_rec_name}&) = delete;\n")
            if record_info.is_nocopy and not record_info.has_del:
                out.write(f"{INDENT}{cpp_rec_name}({cpp_rec_name}&&) = default;\n")
                out.write(f"{INDENT}{cpp_rec_name}& operator=({cpp_rec_name}&&) = default;\n")

        # Generate destructor if __del__ is defined
        self._gen_move_and_destructor(out, record)

        # Generate converting move constructor for covariant generics
        self._gen_covariant_converting_ctor(out, record)

        # Synthesize __iter__() -> self for iterator types (has __next__ but no explicit __iter__)
        has_next = any(m.name == "__next__" for m in record.methods)
        has_iter = any(m.name == "__iter__" for m in record.methods)
        if has_next and not has_iter:
            out.write(f"\n{INDENT}auto& __iter__() {{ return *this; }}\n")

        # Synthesize begin()/end() for types with __iter__() -> SpanIter[T].
        # Makes the container satisfy input_range for NativeIterable concept.
        # Must appear before user methods so that concept constraints see a
        # consistent declaration state when evaluated inside method bodies.
        # __iter__() is declared later in the class, but C++ inline member
        # bodies see all members regardless of textual order.
        # has_iter reflects user-defined __iter__ only; the synthesized
        # __iter__() for pure iterators (above) is intentionally excluded.
        if has_iter:
            has_begin = self._has_method_or_field(record, "begin")
            has_end = self._has_method_or_field(record, "end")
            if not has_begin and not has_end:
                record_info = self.ctx.analyzer.registry.get_record(record.name)
                if record_info and "__iter__" in record_info.methods:
                    iter_overloads = record_info.methods["__iter__"]
                    if iter_overloads and isinstance(iter_overloads[0].return_type, SpanIterType):
                        out.write(f"\n{INDENT}auto begin() {{ return this->__iter__().begin(); }}\n")
                        out.write(f"{INDENT}auto end() {{ return this->__iter__().end(); }}\n")
                        out.write(f"{INDENT}auto begin() const {{ return this->__iter__().begin(); }}\n")
                        out.write(f"{INDENT}auto end() const {{ return this->__iter__().end(); }}\n")

        # Generate methods (excluding __init__ and __del__)
        dynamic_overrides = self.functions._get_dynamic_override_info(record.name)
        # Track names already dispatched via @overload so that the const clone of a
        # @auto_readonly @overload implementation is not emitted as a plain method.
        # (The mutable clone emits specialized methods for all stubs including const ones.)
        overload_dispatched: set[str] = set()
        for method in record.methods:
            if method.name in ("__init__", "__del__"):
                continue
            # Skip @overload stubs -- the implementation emits all overloads
            if method.is_overload_stub:
                continue
            # Generator methods: emit inline (simple) or declaration-only (complex)
            if method.is_generator:
                from .gen_generators import GeneratorCodegen
                if self.gen_generators.is_simple_generator(method):
                    self.gen_generators.gen_simple_generator_inline(
                        out, method, record_name=record.name)
                else:
                    # Declaration only -- body defined after generator struct
                    struct_name = GeneratorCodegen.gen_struct_name(method, record.name)
                    params = self.functions.gen_params(
                        method.params, method, emit_defaults=True)
                    const_suffix = " const" if method.is_readonly else ""
                    out.write(f"\n{INDENT}{struct_name} {method.name}({params}){const_suffix};\n")
                continue
            # Check if this is an @overload implementation
            overload_stubs = self.ctx.analyzer.overload_groups.get(id(method))
            if overload_stubs:
                overload_dispatched.add(method.name)
                if self.functions._overload_stubs_are_literal_only(overload_stubs, method):
                    for stub in overload_stubs:
                        self.functions._gen_literal_specialized_method(
                            out, method, stub, record.name,
                            record_type_param_bounds=record.type_param_bounds or None,
                            dynamic_overrides=dynamic_overrides,
                        )
                else:
                    for stub in overload_stubs:
                        self.functions._gen_overload_specialized_method(
                            out, method, stub, record.name,
                            record_type_param_bounds=record.type_param_bounds or None,
                            dynamic_overrides=dynamic_overrides,
                        )
            elif method.name in overload_dispatched:
                # Const clone of a @auto_readonly @overload impl -- already emitted above.
                pass
            else:
                self.functions.gen_method_def(out, method, record.name, dynamic_overrides,
                                                record_type_param_bounds=record.type_param_bounds or None)

        # Generate const operator[] for subscript read syntax (obj[i])
        self._gen_subscript_operators(out, record)

        # Generate binary operators from dunder methods (enables protocol conformance)
        self._gen_binary_operators(out, record)

        # Generate operator*() for types with __deref__ (C++ interop)
        self._gen_deref_operators(out, record)

        # Generate size() from __len__ for STL compatibility
        self._gen_size_method(out, record)

        # Generate operator() from __call__ (callable objects)
        self._gen_call_operator(out, record)

        # Generate unary operators from dunder methods
        self._gen_unary_operators(out, record)

        out.write("};\n")
        self._gen_record_ostream(out, record)

    def _gen_record_ostream(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator<< overload for printing a record."""
        name = escape_cpp_name(record.name)

        # Check if record defines its own __str__ or __repr__ -- delegate if so.
        # Only check direct methods (not inherited) so that e.g. BaseException.__str__
        # doesn't override the field-by-field printer for user exception subclasses.
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        has_str = False
        has_repr = False
        if record_info:
            has_str = bool(record_info.get_method_overloads("__str__"))
            if not has_str:
                has_repr = bool(record_info.get_method_overloads("__repr__"))
        # For template structs, generate a template operator<<
        if record.type_params:
            # Build params respecting INT type params
            params_parts = []
            for i, p in enumerate(record.type_params):
                kind = record.type_param_kinds[i] if record.type_param_kinds and i < len(record.type_param_kinds) else TypeParamKind.TYPE
                if kind == TypeParamKind.INT:
                    params_parts.append(f"std::size_t {p}")
                else:
                    params_parts.append(f"typename {p}")
            params = ", ".join(params_parts)
            type_args = ", ".join(record.type_params)
            out.write(f"\ntemplate<{params}>\n")
            out.write(f"inline std::ostream& operator<<(std::ostream& os, const {name}<{type_args}>& obj) {{\n")
        else:
            out.write(f"\ninline std::ostream& operator<<(std::ostream& os, const {name}& obj) {{\n")

        if has_str:
            out.write(f"{INDENT}os << obj.__str__();\n")
        elif has_repr:
            out.write(f"{INDENT}os << obj.__repr__();\n")
        else:
            out.write(f'{INDENT}::tpy::print_object_default(os, "{record.name}", obj);\n')

        out.write(f"{INDENT}return os;\n")
        out.write("}\n")

    def _is_super_init_call(self, stmt: TpyStmt) -> bool:
        """Check if a statement is a super().__init__() call."""
        if isinstance(stmt, TpyExprStmt):
            expr = stmt.expr
            if isinstance(expr, TpyMethodCall) and expr.method == "__init__":
                if expr.super_parent_type is not None:
                    return True
        return False

    def _gen_move_and_destructor(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a C++ destructor with drop-flag protection from a __del__ method.

        Emits:
        1. Custom move constructor that sets source's __tpy_owned_ = false
        2. Custom move assignment via destroy-and-reconstruct (runs destructor on
           old value, then placement-new move-constructs the new value)
        3. Destructor guarded by __tpy_owned_ to skip body on moved-from objects

        super().__del__() calls are dropped -- parent destructors are called
        automatically by C++ after the child destructor body runs.
        """
        del_method = record.del_method
        if del_method is None:
            return

        name = record.name
        cpp_name = escape_cpp_name(name)
        record_info = self.ctx.analyzer.registry.get_record(name)

        # Filter out super().__del__() calls -- they're automatic in C++
        body_stmts = [s for s in del_method.body if not is_super_del_call(s)]

        # --- Custom move constructor ---
        init_parts = []
        if record_info and record_info.parent:
            parent_cpp = record_info.parent.to_cpp()
            init_parts.append(f"{parent_cpp}(std::move(other))")
        for fld in record.fields:
            cpp_fld = escape_cpp_name(fld.name)
            init_parts.append(f"{cpp_fld}(std::move(other.{cpp_fld}))")

        init_list = ""
        if init_parts:
            init_list = " : " + ", ".join(init_parts)

        out.write(f"{INDENT}{cpp_name}({cpp_name}&& other) noexcept{init_list} {{\n")
        out.write(f"{INDENT}{INDENT}other.__tpy_owned_ = false;\n")
        out.write(f"{INDENT}}}\n")

        # --- Custom move assignment (destroy-and-reconstruct) ---
        out.write(f"{INDENT}{cpp_name}& operator=({cpp_name}&& other) noexcept {{\n")
        out.write(f"{INDENT}{INDENT}if (this != &other) {{\n")
        out.write(f"{INDENT}{INDENT}{INDENT}this->~{cpp_name}();\n")
        out.write(f"{INDENT}{INDENT}{INDENT}new (this) {cpp_name}(std::move(other));\n")
        out.write(f"{INDENT}{INDENT}}}\n")
        out.write(f"{INDENT}{INDENT}return *this;\n")
        out.write(f"{INDENT}}}\n")

        # --- Destructor with drop-flag guard ---
        self.ctx.emit_preceding_comments(out, del_method.loc, indent=INDENT)
        self.ctx.emit_source_comment(out, del_method.loc, indent=INDENT)
        out.write(f"\n{INDENT}~{cpp_name}() {{\n")
        out.write(f"{INDENT}{INDENT}if (!this->__tpy_owned_) return;\n")
        if body_stmts:
            local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
            local_ns.bind_variable("self", NamedType(name))
            self.functions.gen_body(out, body_stmts, [], del_method.return_type,
                                    del_method, local_ns, indent_level=2, is_method=True,
                                    record_type_param_bounds=record.type_param_bounds or None)
        out.write(f"{INDENT}}}\n")

    def _gen_covariant_converting_ctor(self, out: TextIO, record: TpyRecord) -> None:
        """Generate converting move constructor for covariant generic types.

        For Box[T] with Covariant[T], emits a template constructor that accepts
        Box<U>&& when U inherits T, enabling Box[Child] -> Box[Parent] conversion.
        """
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        if not record_info or not record_info.type_params:
            return
        covariant = get_covariant_params(record_info)
        if not covariant:
            return

        cpp_name = escape_cpp_name(record.name)

        # Build template params and requires clause
        other_params = []
        requires_parts = []
        for i, tp in enumerate(record_info.type_params):
            kind = (record_info.type_param_kinds[i]
                    if record_info.type_param_kinds and i < len(record_info.type_param_kinds)
                    else TypeParamKind.TYPE)
            u_name = f"__CovU_{tp}"
            if kind == TypeParamKind.INT:
                other_params.append(f"std::size_t {u_name}")
            else:
                other_params.append(f"typename {u_name}")
            if kind == TypeParamKind.TYPE:
                if tp in covariant:
                    requires_parts.append(f"std::is_base_of_v<{tp}, {u_name}>")
                else:
                    requires_parts.append(f"std::is_same_v<{tp}, {u_name}>")

        if not requires_parts:
            return

        other_type_args = ", ".join(
            f"__CovU_{tp}" for tp in record_info.type_params
        )
        template_str = ", ".join(other_params)
        requires_str = " && ".join(requires_parts)

        # Build member init list: transfer each field from __other
        init_parts = []
        if record_info.parent:
            parent_cpp = self.types.type_to_cpp(record_info.parent)
            init_parts.append(f"{parent_cpp}(std::move(__other))")
        for fld in record.fields:
            cpp_fld = escape_cpp_name(fld.name)
            init_parts.append(f"{cpp_fld}(std::move(__other.{cpp_fld}))")
        init_list = ""
        if init_parts:
            init_list = " : " + ", ".join(init_parts)

        out.write(f"\n{INDENT}template<{template_str}>\n")
        out.write(f"{INDENT}{INDENT}requires ({requires_str})\n")
        out.write(f"{INDENT}{cpp_name}({cpp_name}<{other_type_args}>&& __other) noexcept{init_list} {{\n")
        if record_info.has_del:
            out.write(f"{INDENT}{INDENT}__other.__tpy_owned_ = false;\n")
        out.write(f"{INDENT}}}\n")

        # Friend declaration for cross-instantiation member access
        friend_tparams = ", ".join("typename" for _ in record_info.type_params)
        out.write(f"{INDENT}template<{friend_tparams}> friend struct {cpp_name};\n")

    def _extract_base_init(self, init_method: TpyFunction, record: TpyRecord) -> str | None:
        """Extract super().__init__() call and return base class initializer string.

        Returns the C++ base initializer (e.g., "Animal(name, age)") or None if
        no super().__init__() is present.
        """
        for stmt in init_method.body:
            if self._is_super_init_call(stmt):
                if not isinstance(stmt, TpyExprStmt):
                    raise CodeGenError("Expected expression statement for super().__init__()", stmt.loc)
                expr = stmt.expr
                if not isinstance(expr, TpyMethodCall):
                    raise CodeGenError("Expected method call for super().__init__()", stmt.loc)
                parent_type = expr.super_parent_type
                args = ", ".join(self.expressions.gen_expr(a) for a in expr.args)
                return f"{parent_type.to_cpp()}({args})"
        return None

    def _extract_field_inits(self, init_method: TpyFunction, record: TpyRecord) -> list[tuple[str, str]]:
        """Extract field initializations from __init__ body.

        Only extracts initializations for fields that belong to this class directly,
        not inherited fields. Inherited field assignments must go in the constructor body.
        """
        # Build field name -> type map for target type passing
        field_types = {fld.name: fld.type for fld in record.fields}
        own_field_names = set(field_types.keys())
        param_names = {p[0] for p in init_method.params}
        # Temporarily populate movable_locals with constructor Own params so that
        # gen_call_arg/_maybe_move can emit std::move() for last-use args inside
        # init list expressions (e.g. Box.from_optional(next)).
        saved_movable = self.ctx.movable_locals.copy()
        for pname, ptype in init_method.params:
            actual = unwrap_readonly(ptype)
            own = unwrap_optional_own(actual)
            if own is not None and not own.wrapped.is_value_type():
                self.ctx.movable_locals.add(pname)
        # Collect nested def names -- these are lambdas defined in the body,
        # so field inits referencing them must go in the body, not the init list.
        nested_def_names = {
            s.func.name for s in init_method.body if isinstance(s, TpyNestedDef)
        }
        try:
            inits = []
            for stmt in init_method.body:
                if isinstance(stmt, TpyAssign):
                    if isinstance(stmt.target, TpyFieldAccess):
                        if isinstance(stmt.target.obj, TpyName) and stmt.target.obj.name == "self":
                            field_name = stmt.target.field
                            source_expr = stmt.value
                            while isinstance(source_expr, TpyCoerce):
                                source_expr = source_expr.expr
                            # Skip if value is a nested def (lambda defined in body)
                            if isinstance(source_expr, TpyName) and source_expr.name in nested_def_names:
                                continue
                            # Skip if value references a body-local variable
                            # (not available in the C++ member initializer list)
                            if isinstance(source_expr, TpyName) and source_expr.name not in param_names:
                                continue
                            # Only add to member init list if it's this class's own field
                            if field_name in own_field_names:
                                fld_type = field_types[field_name]
                                # Unwrap copy() in member init -- init list copies implicitly
                                source = self.ctx.unwrap_copy(stmt.value)
                                value = self.expressions.gen_expr(source, fld_type)
                                # Auto-move Own[T] params at last use in member init list.
                                inner = source
                                while isinstance(inner, TpyCoerce):
                                    inner = inner.expr
                                if (isinstance(inner, TpyName)
                                        and id(inner) in self.ctx.analyzer.ctx.all_last_uses):
                                    for pname, ptype in init_method.params:
                                        if pname == inner.name and isinstance(unwrap_readonly(ptype), OwnType):
                                            value = f"std::move({value})"
                                            break
                                # T* sources need conversion to std::optional<T>; field access (std::optional<T>) doesn't.
                                # OwnType(OptionalType) params are std::optional<T>&& -- already optional, no conversion.
                                if isinstance(fld_type, OptionalType) and fld_type.uses_pointer_repr():
                                    # Check if the source param is Own[Optional[T]] -- then the C++ param
                                    # is std::optional<T>&& and no ptr_to_optional is needed.
                                    source_is_own_optional = False
                                    source = self.ctx.unwrap_copy(stmt.value)
                                    if isinstance(source, TpyName):
                                        for pname, ptype in init_method.params:
                                            if pname == source.name and isinstance(ptype, OwnType):
                                                source_is_own_optional = True
                                                break
                                    if not source_is_own_optional and not isinstance(source, TpyFieldAccess):
                                        raw_val_type = self.ctx.get_expr_type(stmt.value)
                                        val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                                        if isinstance(val_type, OptionalType):
                                            if not (isinstance(val_type.inner, OwnType)):
                                                value = f"::tpy::ptr_to_optional({value})"
                                # Pointer-variant param -> value-variant field: deref+copy.
                                # The param is variant<T*...> but the field stores variant<T...>.
                                if isinstance(fld_type, UnionType) and fld_type.uses_pointer_repr():
                                    val_cpp = self.types.type_to_cpp(fld_type)
                                    value = f"::tpy::to_value_variant<{val_cpp}>({value})"
                                inits.append((field_name, value))
            return inits
        finally:
            self.ctx.movable_locals = saved_movable

    def _all_fields_default_constructible(self, record: TpyRecord) -> bool:
        """Check if all own fields and the parent (if any) are C++-default-constructible.

        Used to decide whether to emit ClassName() = default;. If any field
        or the parent class lacks a C++ default constructor, = default would
        fail to compile.

        For generic records (template classes) we always emit = default: C++ will
        implicitly delete it at the instantiation point if a type arg is not
        default-constructible, which is the correct behaviour.

        Uses C++-level constructibility: a user record is C++-default-constructible
        if it has no __init__ (aggregate) or if all its fields are themselves
        C++-default-constructible (so it will also emit = default).
        """
        # Template classes: C++ handles the constraint at instantiation time.
        if record.type_params:
            return True
        if not all(self._fld_type_cpp_default_constructible(fld.type) for fld in record.fields):
            return False
        record_info = self.ctx.analyzer.ctx.registry.get_record(record.name)
        if record_info is not None and record_info.parent is not None:
            if not self._fld_type_cpp_default_constructible(record_info.parent):
                return False
        return True

    def _fld_type_cpp_default_constructible(self, typ: TpyType) -> bool:
        """Check if a TPy type maps to a C++-default-constructible type.

        For non-record types and generic instantiations delegates to the
        sema-level _is_default_constructible (primitives, containers, Optional,
        and records-with-all-default-params all return True there).

        For non-generic user records, checks C++-level constructibility
        recursively: a record with __init__ is C++-default-constructible if
        all its own fields are too (meaning it will also emit = default).
        """
        protocols = self.ctx.analyzer.protocols
        # Tuple/Array: must check element types with C++-level logic because
        # std::tuple<T>/std::array<T,N> are default-constructible iff T is.
        if isinstance(typ, TupleType):
            return all(self._fld_type_cpp_default_constructible(et) for et in typ.element_types)
        if isinstance(typ, ArrayType):
            elem = typ.get_element_type()
            return elem is not None and self._fld_type_cpp_default_constructible(elem)
        # Enum types map to C++ enum class, which is trivially constructible.
        if isinstance(typ, EnumType):
            return True
        # Raw pointers are trivially constructible (just uninitialized).
        if isinstance(typ, PtrType):
            return True
        if not isinstance(typ, NamedType) or not typ.is_user_record:
            return protocols._is_default_constructible(typ)
        # Generic instantiation (e.g. Pair[Int32]): the base template class always
        # emits = default (see _all_fields_default_constructible), so any instantiation
        # is C++-default-constructible. Non-generic instantiations fall through below.
        if typ.type_args:
            base_rec = self.ctx.analyzer.ctx.registry.get_record(typ.name)
            if base_rec is not None and base_rec.type_params:
                return True  # generic class always emits = default
            return protocols._is_default_constructible(typ)
        record_info = self.ctx.analyzer.ctx.registry.get_record(typ.name)
        if record_info is None:
            return False
        if not record_info.has_init:
            return True  # aggregate: always C++ default-constructible
        # Non-generic record with __init__: default-constructible iff parent and all own fields are
        if record_info.parent is not None:
            if not self._fld_type_cpp_default_constructible(record_info.parent):
                return False
        return all(self._fld_type_cpp_default_constructible(f.type) for f in record_info.fields)

    def _get_non_init_stmts(self, init_method: TpyFunction, record: TpyRecord) -> list[TpyStmt]:
        """Get statements from __init__ that aren't simple field assignments.

        These need to go in the constructor body, not the initializer list.
        Includes assignments to inherited fields (they can't be in the member init list).
        Skips super().__init__() calls (handled separately in base initializer).
        """
        # Get the set of this record's own field names
        own_field_names = {fld.name for fld in record.fields}
        param_names = {p[0] for p in init_method.params}
        # Nested def names -- field assignments referencing these go in the body
        nested_def_names = {
            s.func.name for s in init_method.body if isinstance(s, TpyNestedDef)
        }

        non_init = []
        for stmt in init_method.body:
            # Skip super().__init__() calls - handled as base initializer
            if self._is_super_init_call(stmt):
                continue
            is_own_field_init = False
            if isinstance(stmt, TpyAssign):
                if isinstance(stmt.target, TpyFieldAccess):
                    if isinstance(stmt.target.obj, TpyName) and stmt.target.obj.name == "self":
                        field_name = stmt.target.field
                        # Only skip if it's this class's own field, not a nested
                        # def ref, and not a body-local variable ref (must mirror
                        # _extract_field_inits guards exactly).
                        if field_name in own_field_names:
                            source_expr = stmt.value
                            while isinstance(source_expr, TpyCoerce):
                                source_expr = source_expr.expr
                            is_nested = isinstance(source_expr, TpyName) and source_expr.name in nested_def_names
                            is_body_local = isinstance(source_expr, TpyName) and source_expr.name not in param_names
                            if not is_nested and not is_body_local:
                                is_own_field_init = True
            if not is_own_field_init:
                non_init.append(stmt)
        return non_init

    def _gen_subscript_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator[] for subscript read syntax.

        For readonly __getitem__: dual overloads (const + non-const) matching
        the method itself. Writes go through ::tpy::__setitem__.
        For non-readonly __getitem__: non-const only (method mutates self).
        For @overload __getitem__: generate operator[] for each stub.
        For @auto_readonly __getitem__ clone pairs: const first, then mutable,
        one per clone (no duplicate).
        """
        # Gather all non-stub __getitem__ implementations.
        # @auto_readonly cloning produces two: one mutable, one const.
        getitem_impls = [
            m for m in record.methods
            if m.name == "__getitem__" and not m.is_overload_stub
        ]

        if not getitem_impls:
            return

        # Check if this __getitem__ has @overload stubs (use first impl for lookup)
        overload_stubs = self.ctx.analyzer.overload_groups.get(id(getitem_impls[0]))
        if overload_stubs:
            self._gen_overload_subscript_operators(out, overload_stubs)
        elif len(getitem_impls) == 2:
            # auto_readonly clone pair: generate const first, then mutable.
            # The const clone generates only the const operator (not the dual non-const),
            # and the mutable clone generates only the mutable operator.
            const_impl = next((m for m in getitem_impls if m.is_readonly), None)
            mutable_impl = next((m for m in getitem_impls if not m.is_readonly), None)
            if const_impl and mutable_impl:
                self._gen_const_subscript_operator(out, const_impl)
                self._gen_mutable_subscript_operator(out, mutable_impl)
            else:
                self._gen_single_subscript_operator(out, getitem_impls[0])
        else:
            self._gen_single_subscript_operator(out, getitem_impls[0])

    def _gen_overload_subscript_operators(self, out: TextIO, stubs: list) -> None:
        """Generate operator[] for @overload __getitem__, handling auto_readonly clone pairs.

        Stubs may include mutable+const clone pairs (from @overload @auto_readonly).
        For each unique parameter type: if both mutable and const clones exist, generate
        const first then mutable; otherwise delegate to _gen_single_subscript_operator.
        """
        # Group stubs by their first parameter type to detect clone pairs.
        # Use param type string as key since TpyType equality works correctly.
        by_param: dict[str, list] = defaultdict(list)
        for stub in stubs:
            if stub.params:
                key = str(stub.params[0][1])
                by_param[key].append(stub)
            else:
                self._gen_single_subscript_operator(out, stub)

        for param_type_str, group in by_param.items():
            if len(group) == 2:
                const_stub = next((m for m in group if m.is_readonly), None)
                mutable_stub = next((m for m in group if not m.is_readonly), None)
                if const_stub and mutable_stub:
                    # Clone pair: const operator first, then mutable
                    self._gen_const_subscript_operator(out, const_stub)
                    # Mutable only if return could be a reference
                    needs_dual = (not mutable_stub.return_type.is_value_type()
                                  or isinstance(mutable_stub.return_type, TypeParamRef))
                    if needs_dual:
                        self._gen_mutable_subscript_operator(out, mutable_stub)
                    continue
            # Single stub (no clone pair): use standard logic
            for stub in group:
                self._gen_single_subscript_operator(out, stub)

    def _gen_single_subscript_operator(self, out: TextIO, method: 'TpyFunction') -> None:
        """Generate a single operator[] overload delegating to __getitem__."""
        if not method.params:
            return
        if method.is_readonly:
            self._gen_const_subscript_operator(out, method)
            # Non-const overload only when return could be a reference
            needs_dual = not method.return_type.is_value_type() or isinstance(method.return_type, TypeParamRef)
            if needs_dual:
                self._gen_mutable_subscript_operator(out, method)
        else:
            self._gen_mutable_subscript_operator(out, method)

    def _gen_const_subscript_operator(self, out: TextIO, method: 'TpyFunction') -> None:
        """Generate a const operator[] overload (read-only subscript)."""
        if not method.params:
            return
        index_param_name, index_type = method.params[0]
        index_cpp = index_type.to_cpp()
        ret_const = method.return_type.to_cpp_return_const()
        out.write(f"\n{INDENT}{ret_const} operator[]({index_cpp} {index_param_name}) const {{\n")
        out.write(f"{INDENT}{INDENT}return __getitem__({index_param_name});\n")
        out.write(f"{INDENT}}}\n")

    def _gen_mutable_subscript_operator(self, out: TextIO, method: 'TpyFunction') -> None:
        """Generate a mutable (non-const) operator[] overload."""
        if not method.params:
            return
        index_param_name, index_type = method.params[0]
        index_cpp = index_type.to_cpp()
        ret_mut = method.return_type.to_cpp_return()
        out.write(f"\n{INDENT}{ret_mut} operator[]({index_cpp} {index_param_name}) {{\n")
        out.write(f"{INDENT}{INDENT}return __getitem__({index_param_name});\n")
        out.write(f"{INDENT}}}\n")

    def _gen_binary_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate C++ operators from dunder methods (arithmetic and comparison).

        This enables user records to conform to C++ concepts that use operator syntax
        (e.g., `t + other`, `t < other`) rather than method calls (e.g., `t.__add__(other)`).
        """
        for method in record.methods:
            if method.name not in DUNDER_TO_BINARY_OP:
                continue
            if not method.params:
                continue  # Binary operators need at least one parameter

            cpp_op = DUNDER_TO_BINARY_OP[method.name]
            param_name, param_type = method.params[0]
            param_cpp = param_type.to_cpp_const_param(param_name)

            # Return type - use to_cpp() for value/Own types
            ret_cpp = method.return_type.to_cpp()

            # Generate friend operator that delegates to the dunder method
            # Using friend function allows symmetric operand handling
            out.write(f"\n{INDENT}friend {ret_cpp} operator{cpp_op}(const {escape_cpp_name(record.name)}& lhs, {param_cpp}) {{\n")
            out.write(f"{INDENT}{INDENT}return lhs.{method.name}({param_name});\n")
            out.write(f"{INDENT}}}\n")

    def _gen_call_operator(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator() delegating to __call__ (callable objects).

        Enables obj(args) syntax and std::invocable concept conformance.
        """
        for method in record.methods:
            if method.name != "__call__":
                continue
            ret_cpp = method.return_type.to_cpp()
            const_suffix = " const" if method.is_readonly else ""
            params_cpp = ", ".join(
                p_type.to_cpp_const_param(p_name)
                for p_name, p_type in method.params
            )
            arg_names = ", ".join(p_name for p_name, _ in method.params)
            out.write(f"\n{INDENT}{ret_cpp} operator()({params_cpp}){const_suffix} {{\n")
            out.write(f"{INDENT}{INDENT}return __call__({arg_names});\n")
            out.write(f"{INDENT}}}\n")

    def _gen_deref_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator*() for types with __deref__().

        Enables C++ interop: *box instead of box.__deref__().
        Only for user-defined types -- Ptr[T] and Ptr[readonly[T]] map to raw T*
        which already support *ptr natively.
        """
        deref_method = None
        for method in record.methods:
            if method.name == "__deref__":
                deref_method = method
                break
        if deref_method is None:
            return

        out.write(f"\n{INDENT}auto operator*() -> decltype(__deref__()) {{\n")
        out.write(f"{INDENT}{INDENT}return __deref__();\n")
        out.write(f"{INDENT}}}\n")

    def _has_method_or_field(self, record: TpyRecord, name: str) -> bool:
        """Check if a record has a method or field with the given name."""
        return (any(m.name == name for m in record.methods)
                or any(f.name == name for f in record.fields))

    def _gen_size_method(self, out: TextIO, record: TpyRecord) -> None:
        """Generate size() from __len__ for STL compatibility.

        Enables user types with __len__ to work with C++ algorithms and
        container concepts that expect a size() method.
        """
        len_method = None
        for m in record.methods:
            if m.name == "__len__":
                len_method = m
                break
        if len_method is None:
            return
        if self._has_method_or_field(record, "size"):
            return
        ret_cpp = len_method.return_type.to_cpp()
        ret_type = len_method.return_type
        is_signed = isinstance(ret_type, BigIntType) or (
            isinstance(ret_type, FixedIntType) and ret_type.signed
        )
        if ret_cpp == "size_t":
            out.write(f"\n{INDENT}size_t size() const {{ return __len__(); }}\n")
        elif is_signed:
            out.write(f"\n{INDENT}size_t size() const {{\n")
            out.write(f"{INDENT}{INDENT}auto len = __len__();\n")
            out.write(f"{INDENT}{INDENT}if (len < 0) ::tpy::tpy_panic(\"__len__ returned negative value\");\n")
            out.write(f"{INDENT}{INDENT}return static_cast<size_t>(len);\n")
            out.write(f"{INDENT}}}\n")
        else:
            out.write(f"\n{INDENT}size_t size() const {{ return static_cast<size_t>(__len__()); }}\n")

    def _gen_unary_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate C++ unary operators from dunder methods."""
        DUNDER_TO_UNARY_OP = {
            "__neg__": "-", "__pos__": "+", "__invert__": "~",
        }
        for method in record.methods:
            if method.name not in DUNDER_TO_UNARY_OP:
                continue
            if method.params:
                continue  # Unary operators take no params
            cpp_op = DUNDER_TO_UNARY_OP[method.name]
            ret_cpp = method.return_type.to_cpp()
            out.write(f"\n{INDENT}friend {ret_cpp} operator{cpp_op}(const {escape_cpp_name(record.name)}& operand) {{\n")
            out.write(f"{INDENT}{INDENT}return operand.{method.name}();\n")
            out.write(f"{INDENT}}}\n")
