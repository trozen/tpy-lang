"""
TurboPython Record Code Generation

Generates C++ structs from TurboPython records.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, StrType, BoolType, FloatType, OptionalType, OwnType, ReadonlyType,
    TypeParamRef, TypeParamKind, RecordInfo,
    ListType, ArrayType, SpanType, unwrap_readonly, unwrap_optional_own, is_any_str_type,
)
from ..parse import (
    TpyRecord, TpyFunction, TpyStmt, TpyExprStmt, TpyAssign,
    TpyMethodCall, TpyFieldAccess, TpyName, is_super_del_call,
)
from ..namespace import Namespace

from .context import INDENT, DUNDER_TO_BINARY_OP, CodeGenError, escape_cpp_name

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

    def _is_native(self, record: TpyRecord) -> bool:
        """Check if a record is a native import (no C++ generation needed)."""
        record_info = self.ctx.analyzer.registry.get_record(record.name)
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
            cpp_type = fld.type.to_cpp()
            default = ""
            if fld.default_value is not None:
                default = f" = {fld.default_value}"
            out.write(f"{INDENT}{cpp_type} {escape_cpp_name(fld.name)}{default};\n")

        # Drop flag for classes with __del__ -- prevents double-drop after move.
        # NOTE: in inheritance chains where both parent and child have __del__, each
        # class emits its own __tpy_owned_ (child's shadows parent's). This works
        # because each destructor reads its own class's flag, but it's fragile --
        # ideally only the root __del__ class should emit the flag.
        if record.del_method is not None:
            out.write(f"{INDENT}bool __tpy_owned_ = true;\n")

        out.write("\n")

        # Determine constructor generation strategy
        if record.init_method:
            has_params = bool(record.init_method.params)
            base_init = self._extract_base_init(record.init_method, record)
            inits = self._extract_field_inits(record.init_method, record)
            non_init_stmts = self._get_non_init_stmts(record.init_method, record)

            self.ctx.emit_preceding_comments(out, record.init_method.loc, indent=INDENT)
            self.ctx.emit_source_comment(out, record.init_method.loc, indent=INDENT)
            if has_params:
                # Generate default constructor for C++ compatibility
                out.write(f"{INDENT}{cpp_rec_name}() = default;\n")

                # Generate parameterized constructor from __init__
                cpp_params = self.functions.gen_params(
                    record.init_method.params,
                    record.init_method.type_params,
                    const_params=True,
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
                                         local_ns, indent_level=2, is_method=True)
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

        # Generate methods (excluding __init__ and __del__)
        dynamic_overrides = self.functions._get_dynamic_override_info(record.name)
        for method in record.methods:
            if method.name in ("__init__", "__del__"):
                continue
            self.functions.gen_method_def(out, method, record.name, dynamic_overrides)

        # Generate const operator[] for subscript read syntax (obj[i])
        self._gen_subscript_operators(out, record)

        # Generate binary operators from dunder methods (enables protocol conformance)
        self._gen_binary_operators(out, record)

        # Generate operator*() for types with __deref__ (C++ interop)
        self._gen_deref_operators(out, record)

        out.write("};\n")
        self._gen_record_ostream(out, record)

    def _gen_record_ostream(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator<< overload for printing a record."""
        name = escape_cpp_name(record.name)

        # Check if record has __str__ or __repr__ (including inherited) -- delegate if so
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        has_str = False
        has_repr = False
        if record_info:
            overloads, _ = self.ctx.analyzer.protocols.lookup_record_method_overloads(
                record_info, "__str__")
            has_str = bool(overloads)
            if not has_str:
                overloads, _ = self.ctx.analyzer.protocols.lookup_record_method_overloads(
                    record_info, "__repr__")
                has_repr = bool(overloads)
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
            out.write(f'{INDENT}os << "{record.name}("')

            for i, fld in enumerate(record.fields):
                cpp_fld = escape_cpp_name(fld.name)
                if i > 0:
                    out.write(f'\n{INDENT}   << ", "')
                out.write(f'\n{INDENT}   << "{fld.name}="')
                # Handle strings - quote them
                if is_any_str_type(fld.type):
                    out.write(f' << "\\"" << obj.{cpp_fld} << "\\""')
                elif isinstance(fld.type, OptionalType):
                    inner = fld.type.inner
                    inner_cpp = inner.to_cpp()
                    if isinstance(inner, BoolType):
                        out.write(f' << tpy::print_optional_val<tpy::print_bool, {inner_cpp}>(obj.{cpp_fld})')
                    elif isinstance(inner, FloatType):
                        out.write(f' << tpy::print_optional_val<tpy::print_float, {inner_cpp}>(obj.{cpp_fld})')
                    elif is_any_str_type(inner):
                        # Quoted string: None or "value"
                        out.write(f' << (obj.{cpp_fld}.has_value() ? std::string("\\"") + std::string(obj.{cpp_fld}.value()) + "\\"" : std::string("None"))')
                    else:
                        out.write(f' << tpy::print_optional_val(obj.{cpp_fld})')
                elif isinstance(fld.type, TypeParamRef):
                    # Type parameter - use ValuePrinter which handles both scalars and containers
                    out.write(f' << tpy::ValuePrinter(obj.{cpp_fld})')
                elif isinstance(fld.type, (ListType, ArrayType, SpanType)):
                    # Known iterable container type - use ListPrinter
                    out.write(f' << tpy::ListPrinter(obj.{cpp_fld})')
                elif isinstance(fld.type, NamedType) and fld.type.is_module_type:
                    # Module-defined types may not be printable -- use placeholder
                    out.write(f' << "<{fld.type}>"')
                else:
                    out.write(f' << obj.{cpp_fld}')

            out.write(f'\n{INDENT}   << ")";\n')

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
        out.write(f"{INDENT}{INDENT}if (!__tpy_owned_) return;\n")
        if body_stmts:
            local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
            local_ns.bind_variable("self", NamedType(name))
            self.functions.gen_body(out, body_stmts, [], del_method.return_type,
                                    del_method, local_ns, indent_level=2, is_method=True)
        out.write(f"{INDENT}}}\n")

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
        # Temporarily populate movable_locals with constructor Own params so that
        # gen_call_arg/_maybe_move can emit std::move() for last-use args inside
        # init list expressions (e.g. Box.from_optional(next)).
        saved_movable = self.ctx.movable_locals.copy()
        for pname, ptype in init_method.params:
            actual = unwrap_readonly(ptype)
            own = unwrap_optional_own(actual)
            if own is not None and not own.wrapped.is_value_type():
                self.ctx.movable_locals.add(pname)
        try:
            inits = []
            for stmt in init_method.body:
                if isinstance(stmt, TpyAssign):
                    if isinstance(stmt.target, TpyFieldAccess):
                        if isinstance(stmt.target.obj, TpyName) and stmt.target.obj.name == "self":
                            field_name = stmt.target.field
                            # Only add to member init list if it's this class's own field
                            if field_name in own_field_names:
                                fld_type = field_types[field_name]
                                value = self.expressions.gen_expr(stmt.value, fld_type)
                                # Auto-move Own[T] params at last use in member init list.
                                # Ctor params are by value (T, not T&&) so always use std::move.
                                if (isinstance(stmt.value, TpyName)
                                        and id(stmt.value) in self.ctx.analyzer.ctx.all_last_uses):
                                    for pname, ptype in init_method.params:
                                        if pname == stmt.value.name and isinstance(unwrap_readonly(ptype), OwnType):
                                            value = f"std::move({value})"
                                            break
                                # T* sources need conversion to std::optional<T>; field access (std::optional<T>) doesn't
                                if isinstance(fld_type, OptionalType) and fld_type.uses_pointer_repr():
                                    raw_val_type = self.ctx.get_expr_type(stmt.value)
                                    val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                                    source = self.ctx.unwrap_copy(stmt.value)
                                    if isinstance(val_type, OptionalType) and not isinstance(source, TpyFieldAccess):
                                        # Own[T] | None is already std::optional<T>; T | None is T* needing conversion
                                        if not (isinstance(val_type, OptionalType) and isinstance(val_type.inner, OwnType)):
                                            value = f"tpy::ptr_to_optional({value})"
                                inits.append((field_name, value))
            return inits
        finally:
            self.ctx.movable_locals = saved_movable

    def _get_non_init_stmts(self, init_method: TpyFunction, record: TpyRecord) -> list[TpyStmt]:
        """Get statements from __init__ that aren't simple field assignments.

        These need to go in the constructor body, not the initializer list.
        Includes assignments to inherited fields (they can't be in the member init list).
        Skips super().__init__() calls (handled separately in base initializer).
        """
        # Get the set of this record's own field names
        own_field_names = {fld.name for fld in record.fields}

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
                        # Only skip if it's this class's own field
                        if field_name in own_field_names:
                            is_own_field_init = True
            if not is_own_field_init:
                non_init.append(stmt)
        return non_init

    def _gen_subscript_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator[] for subscript read syntax.

        For readonly __getitem__: dual overloads (const + non-const) matching
        the method itself. Writes go through tpy::__setitem__.
        For non-readonly __getitem__: non-const only (method mutates self).
        """
        getitem = None
        for method in record.methods:
            if method.name == "__getitem__":
                getitem = method
                break

        if getitem is None:
            return

        if not getitem.params:
            return
        index_param_name, index_type = getitem.params[0]
        index_cpp = index_type.to_cpp()

        if getitem.is_readonly:
            ret_const = getitem.return_type.to_cpp_return_const()
            out.write(f"\n{INDENT}{ret_const} operator[]({index_cpp} {index_param_name}) const {{\n")
            out.write(f"{INDENT}{INDENT}return __getitem__({index_param_name});\n")
            out.write(f"{INDENT}}}\n")
            # Non-const overload only when return could be a reference
            needs_dual = not getitem.return_type.is_value_type() or isinstance(getitem.return_type, TypeParamRef)
            if needs_dual:
                ret_mut = getitem.return_type.to_cpp_return()
                out.write(f"\n{INDENT}{ret_mut} operator[]({index_cpp} {index_param_name}) {{\n")
                out.write(f"{INDENT}{INDENT}return __getitem__({index_param_name});\n")
                out.write(f"{INDENT}}}\n")
        else:
            ret_mut = getitem.return_type.to_cpp_return()
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

    def _gen_deref_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator*() for types with __deref__().

        Enables C++ interop: *box instead of box.__deref__().
        Only for user-defined types -- Ptr[T]/ReadOnlyPtr[T] map to raw T*
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
