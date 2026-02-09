"""
TurboPython Record Code Generation

Generates C++ structs from TurboPython records.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    NamedType, StrType, TypeParamRef, TypeParamKind
)
from ..parse import (
    TpyRecord, TpyFunction, TpyStmt, TpyExprStmt, TpyAssign,
    TpyMethodCall, TpyFieldAccess, TpyName
)
from ..namespace import Namespace

from .context import DUNDER_TO_BINARY_OP, CodeGenError

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
        """
        record_by_name = {r.name: r for r in records}

        # Build dependency graph
        # Only consider user-defined parents (NamedType records), not builtin types
        dependencies: dict[str, set[str]] = {r.name: set() for r in records}
        for record in records:
            record_info = self.ctx.analyzer.registry.get_record(record.name)
            if (record_info and record_info.parent and
                isinstance(record_info.parent, NamedType) and record_info.parent.is_record and
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

    def gen_record_decl(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a struct declaration for a record."""
        # Get record info for inheritance information
        record_info = self.ctx.analyzer.registry.get_record(record.name)

        # Generate template prefix for generic records
        if record.type_params:
            template_header = self.protocols.gen_record_template_header(
                record.type_params, record.type_param_bounds, record.type_param_kinds
            )
            out.write(f"{template_header}\n")

        # Generate struct with optional inheritance
        if record_info and record_info.parent:
            out.write(f"struct {record.name} : {record_info.parent.to_cpp()} {{\n")
        else:
            out.write(f"struct {record.name} {{\n")

        # Fields
        for fld in record.fields:
            cpp_type = fld.type.to_cpp()
            default = ""
            if fld.default_value is not None:
                default = f" = {fld.default_value}"
            out.write(f"  {cpp_type} {fld.name}{default};\n")

        out.write("\n")

        # Determine constructor generation strategy
        if record.init_method:
            has_params = bool(record.init_method.params)
            base_init = self._extract_base_init(record.init_method, record)
            inits = self._extract_field_inits(record.init_method, record)
            non_init_stmts = self._get_non_init_stmts(record.init_method, record)

            if has_params:
                # Generate default constructor for C++ compatibility
                out.write(f"  {record.name}() = default;\n")

                # Generate parameterized constructor from __init__
                # Use const ref for object types to allow temporaries like MyRecord([1, 2, 3])
                cpp_params = ", ".join(
                    ptype.to_cpp_const_param(pname)
                    for pname, ptype in record.init_method.params
                )
                out.write(f"  explicit {record.name}({cpp_params})")
            else:
                # No params: generate default constructor with body
                out.write(f"  {record.name}()")

            # Build member init list: base init (if any) + field inits
            all_inits = []
            if base_init:
                all_inits.append(base_init)
            all_inits.extend(f"{name}({val})" for name, val in inits)
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
                out.write("  }\n")
            else:
                out.write(" {}\n")
        else:
            # No __init__, use default constructor
            out.write(f"  {record.name}() = default;\n")

        # Generate methods (excluding __init__)
        for method in record.methods:
            if method.name == "__init__":
                continue
            self.functions.gen_method_def(out, method, record.name)

        # Generate operator[] if __getitem__ exists (enables Sequence protocol conformance)
        self._gen_subscript_operators(out, record)

        # Generate binary operators from dunder methods (enables protocol conformance)
        self._gen_binary_operators(out, record)

        out.write("};\n")
        self._gen_record_ostream(out, record)

    def _gen_record_ostream(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator<< overload for printing a record."""
        name = record.name
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
        out.write(f'  os << "{name}("')

        for i, fld in enumerate(record.fields):
            if i > 0:
                out.write('\n     << ", "')
            out.write(f'\n     << "{fld.name}="')
            # Handle strings - quote them
            if isinstance(fld.type, StrType):
                out.write(f' << "\\"" << obj.{fld.name} << "\\""')
            elif isinstance(fld.type, TypeParamRef):
                # Type parameter - use ValuePrinter which handles both scalars and containers
                out.write(f' << tpy::ValuePrinter(obj.{fld.name})')
            elif fld.type.get_element_type() is not None:
                # Known container type - use ListPrinter
                out.write(f' << tpy::ListPrinter(obj.{fld.name})')
            else:
                out.write(f' << obj.{fld.name}')

        out.write('\n     << ")";\n')
        out.write("  return os;\n")
        out.write("}\n")

    def _is_super_init_call(self, stmt: TpyStmt) -> bool:
        """Check if a statement is a super().__init__() call."""
        if isinstance(stmt, TpyExprStmt):
            expr = stmt.expr
            if isinstance(expr, TpyMethodCall) and expr.method == "__init__":
                if expr.super_parent_type is not None:
                    return True
        return False

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
        # Get the set of this record's own field names
        own_field_names = {fld.name for fld in record.fields}

        inits = []
        for stmt in init_method.body:
            if isinstance(stmt, TpyAssign):
                if isinstance(stmt.target, TpyFieldAccess):
                    if isinstance(stmt.target.obj, TpyName) and stmt.target.obj.name == "self":
                        field_name = stmt.target.field
                        # Only add to member init list if it's this class's own field
                        if field_name in own_field_names:
                            value = self.expressions.gen_expr(stmt.value)
                            inits.append((field_name, value))
        return inits

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
        """Generate operator[] if __getitem__/__setitem__ exist.

        This enables user records to conform to C++ concepts like tpy::Sequence
        which use t[i] syntax rather than t.__getitem__(i).
        """
        getitem = None
        setitem = None
        for method in record.methods:
            if method.name == "__getitem__":
                getitem = method
            elif method.name == "__setitem__":
                setitem = method

        if getitem is None:
            return

        # Get the index parameter type and return type
        if not getitem.params:
            return  # __getitem__ needs at least an index param
        index_param_name, index_type = getitem.params[0]
        index_cpp = index_type.to_cpp()
        # Use to_cpp_return_const() since operator[] is const (returns const ref for objects)
        ret_cpp = getitem.return_type.to_cpp_return_const()

        # Generate const operator[] that delegates to __getitem__
        out.write(f"\n  {ret_cpp} operator[]({index_cpp} {index_param_name}) const {{\n")
        out.write(f"    return __getitem__({index_param_name});\n")
        out.write("  }\n")

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
            out.write(f"\n  friend {ret_cpp} operator{cpp_op}(const {record.name}& lhs, {param_cpp}) {{\n")
            out.write(f"    return lhs.{method.name}({param_name});\n")
            out.write("  }\n")
