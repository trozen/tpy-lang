"""
TurboPython C++ Code Generator

Generates C++ code from the analyzed TurboPython AST:
- Records -> structs with constructors
- Functions -> free functions
- Pointer field access (.) -> arrow operator (->)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TextIO
import io

from .typesys import (
    TpyType, Int32Type, VoidType, RecordType, PtrType, ConstPtrType, OwnType,
    StaticListType, ArrayType, SpanType, ListType, PendingListType, ProtocolType, SelfType,
    StrType, CharType, BoolType, BigIntType, IntLiteralType, FloatType,
    INT32, VOID, BIGINT, FLOAT, CHAR, STR
)
from .namespace import Namespace, BindingKind
from .parse import (
    SourceLocation,
    TpyModule, TpyRecord, TpyFunction, TpyProtocol, TpyStmt, TpyExpr,
    TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn, TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce
)
from .sema import SemanticAnalyzer
from tpyc import modules as builtin_modules


class CodeGenError(Exception):
    """Error during C++ code generation."""
    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc
        super().__init__(message)

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: error: {self.message}"
        return f"{filename}: error: {self.message}"


@dataclass
class CodeGenOptions:
    """Options for C++ code generation."""
    emit_source_comments: bool = False  # Embed Python source as comments in generated C++


class CodeGenerator:
    """Generates C++ code from TurboPython AST."""

    def __init__(self, analyzer: SemanticAnalyzer, options: CodeGenOptions | None = None):
        self.analyzer = analyzer
        self.options = options or CodeGenOptions()
        self.indent_level = 0
        self.module_name = "generated"
        self.declared_vars: set[str] = set()  # Track declared variables in current scope
        self.var_types: dict[str, TpyType] = {}  # Track variable types for code generation
        self.local_scope_names: set[str] = set()  # Local vars/params that shadow globals
        self.source_lines: list[str] = []  # Source lines for emit_source_comments
        # Pending temporaries for array literals passed to mutable Array params
        self._pending_temps: list[tuple[str, str, str]] = []  # [(name, type_cpp, init_expr), ...]
        self._temp_counter = 0
        # Current local namespace for variable tracking (parallels analyzer's namespace)
        self.current_ns: Namespace | None = None

    def _get_resolved_type(self, expr: TpyExpr, target_type: TpyType | None = None) -> TpyType:
        """Get the resolved type of an expression, handling PendingListType.

        PendingListType is used during semantic analysis but should be resolved
        to concrete Array or list types before codegen. This method looks up
        the resolved type if needed.

        Args:
            expr: The expression to get the type of.
            target_type: Optional hint for what type the expression will be coerced to.
                         Used to determine if literal+literal should be Int32 or BigInt.
        """
        # Check for codegen-overridden types (e.g., loop variables)
        if isinstance(expr, TpyName) and expr.name in self.var_types:
            return self.var_types[expr.name]
        if isinstance(expr, TpyCoerce):
            return expr.expected_type

        # For binary operations, compute type using resolved operand types
        if isinstance(expr, TpyBinOp):
            # First pass without context to detect Int32 operands
            left_raw = self._get_resolved_type(expr.left)
            right_raw = self._get_resolved_type(expr.right)

            # If either operand is float, result is float (float takes precedence)
            if isinstance(left_raw, FloatType) or isinstance(right_raw, FloatType):
                # True division always returns float
                if expr.op == "div":
                    return FLOAT
                # Most arithmetic ops with float return float
                if expr.op in ("+", "-", "*", "//", "%", "**"):
                    return FLOAT

            # True division always returns float
            if expr.op == "div":
                return FLOAT

            # Determine Int32 context: explicit target or operand is Int32
            int32_ctx = target_type if isinstance(target_type, Int32Type) else None
            if isinstance(left_raw, Int32Type) or isinstance(right_raw, Int32Type):
                int32_ctx = INT32
            # Second pass with context for proper literal resolution
            left_type = self._get_resolved_type(expr.left, int32_ctx)
            right_type = self._get_resolved_type(expr.right, int32_ctx)
            # Use analyzer types for literal check - analyzer returns IntLiteralType for
            # all-literal expressions (including nested binops like 2+3)
            # NOTE: Variables (TpyName) may have IntLiteralType but aren't actual literals
            left_analyzer_type = self.analyzer.get_expr_type(expr.left)
            right_analyzer_type = self.analyzer.get_expr_type(expr.right)
            left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
            right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)
            # If target is Int32 and both operands are literals, result is Int32
            if isinstance(int32_ctx, Int32Type) and left_is_literal and right_is_literal:
                return INT32
            # If either operand is Int32 (and other is compatible), result is Int32
            if isinstance(left_type, Int32Type) and isinstance(right_type, (Int32Type, IntLiteralType)):
                return INT32
            if isinstance(right_type, Int32Type) and isinstance(left_type, (Int32Type, IntLiteralType)):
                return INT32
            # Otherwise, result is BigInt if either operand is BigInt, or if both are IntLiteral
            is_bigint_op = (
                isinstance(left_type, BigIntType) or
                isinstance(right_type, BigIntType) or
                (isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType))
            )
            if is_bigint_op and expr.op in ("+", "-", "*", "//", "%", "**", "&", "|", "^", "<<", ">>"):
                return BIGINT

        typ = self.analyzer.get_expr_type(expr)
        if isinstance(typ, PendingListType):
            # Look up the resolved type from the literal info
            literal_id = typ.literal_id
            if literal_id in self.analyzer.list_literals:
                info = self.analyzer.list_literals[literal_id]
                if info.resolved_type:
                    return info.resolved_type
            # Fallback: treat as ListType with resolved element type
            elem_type = typ.element_type
            if isinstance(elem_type, IntLiteralType):
                elem_type = INT32
            return ListType(elem_type)
        # Resolve IntLiteralType based on context (Int32 if target, else BigInt)
        if isinstance(typ, IntLiteralType):
            if isinstance(target_type, Int32Type):
                return INT32
            return BIGINT
        # Resolve IntLiteralType in container element types
        if isinstance(typ, ListType) and isinstance(typ.element_type, IntLiteralType):
            return ListType(BIGINT)
        return typ

    def generate(self, module: TpyModule, module_name: str = "generated") -> tuple[str, str]:
        """Generate C++ header and source files.

        Args:
            module: The parsed TurboPython module AST.
            module_name: Name for the generated files (used in #include).
        """
        self.module_name = module_name
        self.source_lines = module.source_lines
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
                        var_type = self._get_resolved_type(stmt.init)
                    seen_globals[stmt.name] = var_type

        # Store global names for use in expression generation (method/field access)
        self.global_names = set(seen_globals.keys())
        # Track if we need synthetic __name__ (add to global_names for proper deref)
        self._has_synthetic_name = "__name__" not in seen_globals
        if self._has_synthetic_name:
            self.global_names.add("__name__")

        # Forward declare records (so global externs can reference them)
        for record in module.records:
            hpp.write(f"struct {record.name};\n")
        if module.records:
            hpp.write("\n")

        # Generate global extern declarations
        # __name__ is always present (synthetic if not user-defined)
        if "__name__" not in seen_globals:
            hpp.write("extern tpy::Global<std::string_view> __name__;\n")
        for stmt in global_decls:
            self._gen_global_extern(hpp, stmt)
        hpp.write("\n")

        # Generate full record definitions
        for record in module.records:
            self._gen_record_decl(hpp, record)
            hpp.write("\n")

        # Generate C++20 concepts for user-defined protocols
        for protocol in module.protocols:
            self._gen_concept_decl(hpp, protocol)
            hpp.write("\n")

        # Generate function declarations
        for func in module.functions:
            self._gen_function_decl(hpp, func)
        hpp.write("\n")

        # Generate global definitions in source (before functions)
        # __name__ is always present (synthetic if not user-defined)
        if "__name__" not in seen_globals:
            cpp.write('tpy::Global<std::string_view> __name__;\n')
        for stmt in global_decls:
            self._gen_global_decl(cpp, stmt)
        cpp.write("\n")

        # Generate function definitions
        for func in module.functions:
            self._gen_function_def(cpp, func)
            cpp.write("\n")

        # Generate module init function and main()
        # Always generate __tpy_init for global initialization (Python semantics)
        # Pass ALL top-level statements to init function (including globals)
        # Note: has_user_main=False because users should call main() explicitly at top level,
        # either as `main()` or `if __name__ == "__main__": main()`
        self._gen_module_init_decl(hpp)
        self._gen_module_init(cpp, module.top_level_stmts, seen_globals, has_user_main=False)
        # Always generate C++ main() that calls __tpy_init
        self._gen_main(cpp)

        self._write_header_epilogue(hpp)

        return hpp.getvalue(), cpp.getvalue()

    def _write_header_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write("#pragma once\n\n")
        out.write('#include "tpy_runtime.hpp"\n\n')
        out.write(f"namespace tpy_user::{self.module_name} {{\n\n")

    def _write_source_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write(f'#include "{self.module_name}.hpp"\n\n')
        out.write(f"namespace tpy_user::{self.module_name} {{\n\n")

    def _write_header_epilogue(self, out: TextIO) -> None:
        out.write(f"}} // namespace tpy_user::{self.module_name}\n")

    def _gen_module_init_decl(self, out: TextIO) -> None:
        """Generate module init function declaration in header."""
        out.write("void __tpy_init();\n")

    def _gen_module_init(self, out: TextIO, stmts: list, global_types: dict[str, TpyType | None] | None = None,
                         has_user_main: bool = False) -> None:
        """Generate module init function containing top-level statements."""
        out.write("void __tpy_init() {\n")
        # Initialize synthetic __name__ if not user-defined
        if self._has_synthetic_name:
            out.write('  __name__ = "__main__";\n')
        # Pre-seed with global names and types so re-declarations become assignments
        if global_types:
            self.declared_vars = set(global_types.keys())
            self.var_types = {name: typ for name, typ in global_types.items() if typ is not None}
        else:
            self.declared_vars = set()
            self.var_types = {}
        # In module init, there are no local shadowing variables
        self.local_scope_names = set()
        # Use global namespace for module init (globals are directly accessible)
        self.current_ns = self.analyzer.global_ns
        self.indent_level = 1
        for stmt in stmts:
            self._gen_stmt(out, stmt)
        self.current_ns = None
        # Call user's main() if defined
        if has_user_main:
            out.write("  main();\n")
        out.write("}\n\n")

    def _gen_main(self, out: TextIO) -> None:
        """Generate C++ main() that calls module init.

        The namespace is closed before main() so main is in global namespace.
        Accepts argc/argv and initializes tpy::sys_argv for sys.argv support.
        """
        out.write(f"}} // namespace tpy_user::{self.module_name}\n\n")
        out.write("int main(int argc, char* argv[]) {\n")
        out.write("  tpy::init_sys_argv(argc, argv);\n")
        out.write(f"  tpy_user::{self.module_name}::__tpy_init();\n")
        out.write("  return 0;\n")
        out.write("}\n")

    def _gen_global_decl(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a global variable definition in source file.

        Globals are wrapped in tpy::Global<T> and declared without initializers.
        Initialization happens in __tpy_init_X() to ensure proper execution order.
        """
        self._emit_source_comment(out, stmt.loc)
        # Use explicit type if provided, otherwise infer from initializer
        if stmt.type:
            var_type = stmt.type
        elif stmt.init:
            var_type = self._get_resolved_type(stmt.init)
        else:
            raise RuntimeError(f"Global '{stmt.name}' has no type and no initializer")
        cpp_type = var_type.to_cpp()
        out.write(f"tpy::Global<{cpp_type}> {stmt.name};\n")

    def _gen_global_extern(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate an extern declaration for a global variable in header file."""
        # Use explicit type if provided, otherwise infer from initializer
        if stmt.type:
            var_type = stmt.type
        elif stmt.init:
            var_type = self._get_resolved_type(stmt.init)
        else:
            raise RuntimeError(f"Global '{stmt.name}' has no type and no initializer")
        cpp_type = var_type.to_cpp()
        out.write(f"extern tpy::Global<{cpp_type}> {stmt.name};\n")

    def _gen_record_decl(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a struct declaration for a record."""
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
            inits = self._extract_field_inits(record.init_method)
            non_init_stmts = self._get_non_init_stmts(record.init_method)

            if has_params:
                # Generate default constructor for C++ compatibility (e.g., StaticList<T>)
                out.write(f"  {record.name}() = default;\n")

                # Generate parameterized constructor from __init__
                # Use const ref for object types to allow temporaries like MyRecord([1, 2, 3])
                params = ", ".join(
                    ptype.to_cpp_const_param(pname)
                    for pname, ptype in record.init_method.params
                )
                out.write(f"  explicit {record.name}({params})")
                if inits:
                    out.write(" : ")
                    out.write(", ".join(f"{name}({val})" for name, val in inits))
                if non_init_stmts:
                    out.write(" {\n")
                    self.declared_vars = {pname for pname, _ in record.init_method.params}
                    self.var_types = {pname: ptype for pname, ptype in record.init_method.params}
                    # Track params as local to prevent false global deref if they shadow globals
                    self.local_scope_names = {pname for pname, _ in record.init_method.params}
                    # Set up local namespace for constructor (bind self and params)
                    local_ns = Namespace(parent=self.analyzer.global_ns)
                    local_ns.bind_variable("self", RecordType(record.name))
                    for pname, ptype in record.init_method.params:
                        local_ns.bind_variable(pname, ptype)
                    self.current_ns = local_ns
                    self.indent_level = 2
                    self.in_method = True
                    for stmt in non_init_stmts:
                        self._gen_stmt(out, stmt)
                    self.in_method = False
                    self.local_scope_names = set()
                    self.current_ns = None
                    self.indent_level = 0
                    out.write("  }\n")
                else:
                    out.write(" {}\n")
            else:
                # No params: generate default constructor with body
                out.write(f"  {record.name}()")
                if inits:
                    out.write(" : ")
                    out.write(", ".join(f"{name}({val})" for name, val in inits))
                if non_init_stmts:
                    out.write(" {\n")
                    self.declared_vars = set()
                    self.var_types = {}
                    self.local_scope_names = set()
                    # Set up local namespace for constructor (bind self)
                    local_ns = Namespace(parent=self.analyzer.global_ns)
                    local_ns.bind_variable("self", RecordType(record.name))
                    self.current_ns = local_ns
                    self.indent_level = 2
                    self.in_method = True
                    for stmt in non_init_stmts:
                        self._gen_stmt(out, stmt)
                    self.in_method = False
                    self.local_scope_names = set()
                    self.current_ns = None
                    self.indent_level = 0
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
            self._gen_method(out, method, record.name)

        # Generate operator[] if __getitem__ exists (enables Sequence protocol conformance)
        self._gen_subscript_operators(out, record)

        # Generate arithmetic operators from dunder methods (enables protocol conformance)
        self._gen_arithmetic_operators(out, record)

        out.write("};\n")
        self._gen_record_ostream(out, record)

    def _gen_record_ostream(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator<< overload for printing a record."""
        name = record.name
        out.write(f"\ninline std::ostream& operator<<(std::ostream& os, const {name}& obj) {{\n")
        out.write(f'  os << "{name}("')

        for i, fld in enumerate(record.fields):
            if i > 0:
                out.write('\n     << ", "')
            out.write(f'\n     << "{fld.name}="')
            # Handle strings - quote them
            if isinstance(fld.type, StrType):
                out.write(f' << "\\"" << obj.{fld.name} << "\\""')
            elif fld.type.get_element_type() is not None:
                # Container - use ListPrinter
                out.write(f' << tpy::ListPrinter(obj.{fld.name})')
            else:
                out.write(f' << obj.{fld.name}')

        out.write('\n     << ")";\n')
        out.write("  return os;\n")
        out.write("}\n")

    def _extract_field_inits(self, init_method: TpyFunction) -> list[tuple[str, str]]:
        """Extract field initializations from __init__ body."""
        inits = []
        for stmt in init_method.body:
            if isinstance(stmt, TpyAssign):
                if isinstance(stmt.target, TpyFieldAccess):
                    if isinstance(stmt.target.obj, TpyName) and stmt.target.obj.name == "self":
                        field_name = stmt.target.field
                        value = self._gen_expr(stmt.value)
                        inits.append((field_name, value))
        return inits

    def _get_non_init_stmts(self, init_method: TpyFunction) -> list[TpyStmt]:
        """Get statements from __init__ that aren't simple field assignments.

        These need to go in the constructor body, not the initializer list.
        """
        non_init = []
        for stmt in init_method.body:
            is_field_init = False
            if isinstance(stmt, TpyAssign):
                if isinstance(stmt.target, TpyFieldAccess):
                    if isinstance(stmt.target.obj, TpyName) and stmt.target.obj.name == "self":
                        is_field_init = True
            if not is_field_init:
                non_init.append(stmt)
        return non_init

    # Methods that should be const (don't mutate self)
    CONST_METHODS = {"__len__", "__getitem__", "__str__", "__repr__", "__hash__", "__eq__", "__ne__",
                     "__lt__", "__le__", "__gt__", "__ge__",
                     # Binary arithmetic operators (return new value, don't modify self)
                     "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
                     "__and__", "__or__", "__xor__", "__lshift__", "__rshift__",
                     # Reverse operators
                     "__radd__", "__rsub__", "__rmul__", "__rtruediv__", "__rfloordiv__", "__rmod__", "__rpow__",
                     # Unary operators
                     "__neg__", "__pos__", "__invert__"}

    def _gen_method(self, out: TextIO, method: TpyFunction, record_name: str) -> None:
        """Generate a method definition inside a struct."""
        is_const = method.name in self.CONST_METHODS
        # Const methods must return const refs for object types
        ret_type = method.return_type.to_cpp_return_const() if is_const else method.return_type.to_cpp_return()
        # Const methods take parameters by const reference
        if is_const:
            params = ", ".join(ptype.to_cpp_const_param(pname) for pname, ptype in method.params)
        else:
            params = self._gen_params(method.params)
        const_suffix = " const" if is_const else ""
        out.write(f"\n  {ret_type} {method.name}({params}){const_suffix} {{\n")

        # Reset declared vars and add parameters
        self.declared_vars = {pname for pname, _ in method.params}
        self.var_types = {pname: ptype for pname, ptype in method.params}
        # Track params as local to prevent false global deref if they shadow globals
        self.local_scope_names = {pname for pname, _ in method.params}

        # Set up local namespace for this method (bind self and params)
        local_ns = Namespace(parent=self.analyzer.global_ns)
        local_ns.bind_variable("self", RecordType(record_name))
        for pname, ptype in method.params:
            local_ns.bind_variable(pname, ptype)
        self.current_ns = local_ns

        self.indent_level = 2
        self.in_method = True
        self.current_return_type = method.return_type
        self.current_func_params = {pname: ptype for pname, ptype in method.params}
        for stmt in method.body:
            self._gen_stmt(out, stmt)
        self.in_method = False
        self.local_scope_names = set()
        self.indent_level = 0
        self.current_ns = None

        out.write("  }\n")

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

    # Mapping from Python dunder methods to C++ binary operators
    # Note: Both __truediv__ and __floordiv__ map to / in C++. For integer types,
    # C++ / is truncating division (like Python //). For user types, they should
    # implement the appropriate semantics in their __truediv__/__floordiv__ methods.
    DUNDER_TO_BINARY_OP = {
        "__add__": "+", "__sub__": "-", "__mul__": "*",
        "__truediv__": "/", "__floordiv__": "/", "__mod__": "%",
        "__and__": "&", "__or__": "|", "__xor__": "^",
        "__lshift__": "<<", "__rshift__": ">>",
    }

    def _gen_arithmetic_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate C++ operators from arithmetic dunder methods.

        This enables user records to conform to C++ concepts that use operator syntax
        (e.g., `t + other`) rather than method calls (e.g., `t.__add__(other)`).
        """
        for method in record.methods:
            if method.name not in self.DUNDER_TO_BINARY_OP:
                continue
            if not method.params:
                continue  # Binary operators need at least one parameter

            cpp_op = self.DUNDER_TO_BINARY_OP[method.name]
            param_name, param_type = method.params[0]
            param_cpp = param_type.to_cpp_const_param(param_name)

            # Return type - use to_cpp() for value/Own types
            ret_cpp = method.return_type.to_cpp()

            # Generate friend operator that delegates to the dunder method
            # Using friend function allows symmetric operand handling
            out.write(f"\n  friend {ret_cpp} operator{cpp_op}(const {record.name}& lhs, {param_cpp}) {{\n")
            out.write(f"    return lhs.{method.name}({param_name});\n")
            out.write("  }\n")

    def _gen_concept_decl(self, out: TextIO, protocol: TpyProtocol) -> None:
        """Generate a C++20 concept for a user-defined protocol.

        SelfType in method signatures is rendered as 'T' (the template parameter).
        This allows the concept to check that e.g., T + T -> T.
        """
        out.write(f"template<typename T>\n")
        out.write(f"concept {protocol.name} = requires(const T& t) {{\n")

        # Mapping from Python dunder methods to C++ operators
        DUNDER_TO_OPERATOR = {
            "__add__": "+", "__sub__": "-", "__mul__": "*",
            "__truediv__": "/", "__floordiv__": "/", "__mod__": "%",
            "__eq__": "==", "__ne__": "!=",
            "__lt__": "<", "__le__": "<=", "__gt__": ">", "__ge__": ">=",
            "__and__": "&", "__or__": "|", "__xor__": "^",
            "__lshift__": "<<", "__rshift__": ">>",
        }

        for method_sig in protocol.methods:
            # Generate requirement for each method
            # SelfType.to_cpp() returns "T", so this handles Self -> T substitution
            ret_cpp = method_sig.return_type.to_cpp()

            # For dunder methods that have tpy:: free function equivalents, use those
            # This allows std types (vector, string, etc.) to satisfy the protocol
            if method_sig.name == "__len__":
                out.write(f"    {{ tpy::__len__(t) }} -> std::convertible_to<{ret_cpp}>;\n")
            elif method_sig.name in DUNDER_TO_OPERATOR and len(method_sig.params) == 1:
                # Binary operators - use C++ operator syntax
                # e.g., __add__(Self) -> Self becomes { t + std::declval<T>() } -> convertible_to<T>
                cpp_op = DUNDER_TO_OPERATOR[method_sig.name]
                _, ptype = method_sig.params[0]
                param_cpp = ptype.to_cpp()
                out.write(f"    {{ t {cpp_op} std::declval<{param_cpp}>() }} -> std::convertible_to<{ret_cpp}>;\n")
            else:
                # { t.method_name(args...) } -> std::convertible_to<return_type>;
                params_str = ""
                if method_sig.params:
                    # Use std::declval for parameter types
                    # SelfType.to_cpp() returns "T", so Self params become std::declval<T>()
                    param_exprs = [f"std::declval<{ptype.to_cpp()}>()" for _, ptype in method_sig.params]
                    params_str = ", ".join(param_exprs)
                out.write(f"    {{ t.{method_sig.name}({params_str}) }} -> std::convertible_to<{ret_cpp}>;\n")

        out.write("};\n")

    def _get_protocol_params(self, params: list[tuple[str, TpyType]]) -> list[tuple[str, ProtocolType]]:
        """Get list of protocol-typed parameters."""
        return [(pname, ptype) for pname, ptype in params if isinstance(ptype, ProtocolType)]

    def _gen_template_header(self, protocol_params: list[tuple[str, ProtocolType]]) -> str:
        """Generate template header with concept constraints for protocol params.

        For non-generic protocols: template<tpy::Sized T_items>
        For generic protocols: template<tpy::Sequence<int32_t> T_items>
        """
        if not protocol_params:
            return ""
        template_parts = []
        for pname, ptype in protocol_params:
            # Look up protocol to get C++ concept name
            protocol_def = builtin_modules.lookup_protocol(ptype.name)
            if protocol_def:
                concept_name = protocol_def.cpp_concept
            else:
                # User-defined protocol - use the protocol name directly
                concept_name = ptype.name

            # For generic protocols, add type arguments
            if ptype.type_args:
                type_args_cpp = ", ".join(t.to_cpp() for t in ptype.type_args)
                template_parts.append(f"{concept_name}<{type_args_cpp}> T_{pname}")
            else:
                template_parts.append(f"{concept_name} T_{pname}")
        return f"template<{', '.join(template_parts)}>\n"

    def _gen_params_with_protocols(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate function parameter list, using template types for protocol params."""
        result = []
        for pname, ptype in params:
            if isinstance(ptype, ProtocolType):
                # Protocol param: const T_name& name
                result.append(f"const T_{pname}& {pname}")
            else:
                result.append(ptype.to_cpp_param(pname))
        return ", ".join(result)

    def _gen_function_decl(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function declaration."""
        protocol_params = self._get_protocol_params(func.params)

        if protocol_params:
            # Template function with concept constraints
            out.write(self._gen_template_header(protocol_params))
            ret_type = func.return_type.to_cpp_return()
            params = self._gen_params_with_protocols(func.params)
            out.write(f"{ret_type} {func.name}({params});\n")
        else:
            ret_type = func.return_type.to_cpp_return()
            params = self._gen_params(func.params)
            out.write(f"{ret_type} {func.name}({params});\n")

    def _gen_function_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function definition."""
        self._emit_source_comment(out, func.loc)

        protocol_params = self._get_protocol_params(func.params)

        if protocol_params:
            # Template function with concept constraints
            out.write(self._gen_template_header(protocol_params))
            ret_type = func.return_type.to_cpp_return()
            params = self._gen_params_with_protocols(func.params)
            out.write(f"{ret_type} {func.name}({params}) {{\n")
        else:
            ret_type = func.return_type.to_cpp_return()
            params = self._gen_params(func.params)
            out.write(f"{ret_type} {func.name}({params}) {{\n")

        # Reset declared vars and add parameters
        self.declared_vars = {pname for pname, _ in func.params}
        self.var_types = {pname: ptype for pname, ptype in func.params}
        # Track local scope names (params + local vars) that shadow globals
        self.local_scope_names = {pname for pname, _ in func.params}

        # Set up local namespace for this function
        local_ns = Namespace(parent=self.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.current_ns = local_ns

        self.indent_level = 1
        self.current_return_type = func.return_type
        self.current_func_params = {pname: ptype for pname, ptype in func.params}
        for stmt in func.body:
            self._gen_stmt(out, stmt)

        self.indent_level = 0
        self.current_ns = None

        out.write("}\n")

    def _gen_params(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate function parameter list."""
        return ", ".join(ptype.to_cpp_param(pname) for pname, ptype in params)

    def _contains_protocol_type(self, typ: TpyType) -> bool:
        """Check if a type contains ProtocolType or SelfType anywhere in its structure.

        Used to determine if a variable should use 'auto' in C++ codegen because
        the actual type depends on template parameters.
        """
        if isinstance(typ, (ProtocolType, SelfType)):
            return True
        elif isinstance(typ, OwnType):
            return self._contains_protocol_type(typ.wrapped)
        elif isinstance(typ, (PtrType, ConstPtrType)):
            return self._contains_protocol_type(typ.pointee)
        elif isinstance(typ, (ListType, SpanType)):
            return self._contains_protocol_type(typ.element_type)
        elif isinstance(typ, (ArrayType, StaticListType)):
            return self._contains_protocol_type(typ.element_type)
        return False

    def _is_temporary_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression produces a temporary (rvalue).

        Temporaries can't bind to non-const lvalue references, so they need
        to be stored in a temp variable when passed to mutable ref params.
        """
        # Literals: [], [1,2,3], [0]*10
        if isinstance(expr, (TpyArrayLiteral, TpyListRepeat)):
            return True
        # Constructor calls: list(), StaticList[T,N](), etc.
        if isinstance(expr, TpyCall) and expr.call_type is not None:
            return True
        return False

    def _emit_source_comment(self, out: TextIO, loc: SourceLocation | None, indent: str = "") -> None:
        """Emit the original Python source line as a comment if enabled."""
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        line_idx = loc.line - 1  # Convert 1-indexed to 0-indexed
        if 0 <= line_idx < len(self.source_lines):
            source_line = self.source_lines[line_idx].rstrip()
            out.write(f"{indent}// {loc.line}: {source_line}\n")

    def _create_temp_for_literal(self, param_type: TpyType, init_expr: str) -> str:
        """Create a temp variable for a literal passed to a mutable reference param.

        Returns the temp variable name to use in the call.
        """
        self._temp_counter += 1
        temp_name = f"__tmp_{self._temp_counter}"
        self._pending_temps.append((temp_name, param_type.to_cpp(), init_expr))
        return temp_name

    def _flush_pending_temps(self, out: TextIO, indent: str) -> None:
        """Emit any pending temp variable declarations."""
        for temp_name, type_cpp, init_expr in self._pending_temps:
            out.write(f"{indent}{type_cpp} {temp_name} = {init_expr};\n")
        self._pending_temps.clear()

    def _gen_stmt(self, out: TextIO, stmt: TpyStmt) -> None:
        """Generate a statement."""
        indent = "  " * self.indent_level

        self._emit_source_comment(out, stmt.loc, indent)

        # Compound statements - delegate to handlers (they flush before their header)
        if isinstance(stmt, TpyIf):
            self._gen_if(out, stmt, indent)
        elif isinstance(stmt, TpyWhile):
            self._gen_while(out, stmt, indent)
        elif isinstance(stmt, TpyFor):
            self._gen_for(out, stmt, indent)
        elif isinstance(stmt, TpyForEach):
            self._gen_for_each(out, stmt, indent)
        else:
            # Simple statements - single flush point for all
            code = self._gen_simple_stmt(stmt, indent)
            if code is not None:
                self._flush_pending_temps(out, indent)
                out.write(code)

    def _gen_simple_stmt(self, stmt: TpyStmt, indent: str) -> str | None:
        """Generate code for simple statements. Returns code to write or None.

        Expression generation happens here (which may create temps).
        The caller handles flushing temps before writing the returned code.
        """
        if isinstance(stmt, TpyVarDecl):
            return self._gen_var_decl_code(stmt, indent)
        elif isinstance(stmt, TpyAssign):
            return self._gen_assign_code(stmt, indent)
        elif isinstance(stmt, TpyAugAssign):
            return self._gen_aug_assign_code(stmt, indent)
        elif isinstance(stmt, TpyExprStmt):
            if isinstance(stmt.expr, TpyStrLiteral):
                return None  # Skip docstrings
            return f"{indent}{self._gen_expr(stmt.expr)};\n"
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                ret_type = self.current_return_type if hasattr(self, 'current_return_type') else None
                return f"{indent}return {self._gen_expr(stmt.value, ret_type)};\n"
            return f"{indent}return;\n"
        elif isinstance(stmt, TpyBreak):
            return f"{indent}break;\n"
        elif isinstance(stmt, TpyContinue):
            return f"{indent}continue;\n"
        return None

    def _gen_var_decl_code(self, stmt: TpyVarDecl, indent: str) -> str | None:
        """Generate code for a variable declaration. Returns code to write or None."""
        # Check if variable is already declared (reassignment)
        if stmt.name in self.declared_vars:
            if stmt.init:
                # For reassignment, use the existing variable's type as target
                var_type = self.var_types.get(stmt.name)
                init_expr = self._gen_expr(stmt.init, var_type)
                return f"{indent}{stmt.name} = {init_expr};\n"
            return None

        # Determine target type for first declaration
        target_type = stmt.type
        if target_type is None and stmt.init:
            # Check if analyzer resolved the type based on usage
            resolved_type = self.analyzer.var_types.get(id(stmt))
            if resolved_type:
                target_type = resolved_type
            else:
                target_type = self.analyzer.get_expr_type(stmt.init)
                # Resolve IntLiteralType to BigInt for standalone variable declarations
                if isinstance(target_type, IntLiteralType):
                    target_type = BIGINT
                # Resolve Array[IntLiteralType] to Array[BigInt]
                elif isinstance(target_type, ArrayType) and isinstance(target_type.element_type, IntLiteralType):
                    target_type = ArrayType(BIGINT, target_type.size)

        # First declaration - track the type and mark as local (shadows globals)
        self.declared_vars.add(stmt.name)
        self.local_scope_names.add(stmt.name)
        self.var_types[stmt.name] = target_type
        # Bind to namespace for global tracking
        if self.current_ns and target_type:
            self.current_ns.bind_variable(stmt.name, target_type)

        if stmt.type:
            # Protocol types use auto in generated code (the actual type is the template param)
            if self._contains_protocol_type(stmt.type):
                cpp_type = "auto"
            else:
                cpp_type = stmt.type.to_cpp()
        elif stmt.init:
            # Check if analyzer resolved the type based on usage
            resolved_type = self.analyzer.var_types.get(id(stmt))
            if resolved_type:
                cpp_type = resolved_type.to_cpp()
            else:
                # Use inferred type from expression
                inferred_type = self.analyzer.get_expr_type(stmt.init)
                if inferred_type is None:
                    raise CodeGenError(
                        f"Could not infer type for variable '{stmt.name}'", loc=stmt.loc
                    )
                # Resolve IntLiteralType to BigInt (Python semantics)
                if isinstance(inferred_type, IntLiteralType):
                    inferred_type = BIGINT
                # Resolve container[IntLiteralType] to container[BigInt]
                elif isinstance(inferred_type, (ArrayType, ListType, PendingListType)):
                    elem = getattr(inferred_type, 'element_type', None)
                    if isinstance(elem, IntLiteralType):
                        if isinstance(inferred_type, ArrayType):
                            inferred_type = ArrayType(BIGINT, inferred_type.size)
                        else:
                            inferred_type = ListType(BIGINT)
                # Protocol types use auto (the actual type is the template param)
                if self._contains_protocol_type(inferred_type):
                    cpp_type = "auto"
                else:
                    cpp_type = inferred_type.to_cpp()
        else:
            raise CodeGenError(f"Variable '{stmt.name}' has no type annotation and no initializer", loc=stmt.loc)

        if stmt.init:
            init_expr = self._gen_expr(stmt.init, target_type)
            return f"{indent}{cpp_type} {stmt.name} = {init_expr};\n"
        else:
            return f"{indent}{cpp_type} {stmt.name};\n"

    def _gen_assign_code(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for an assignment. Returns code to write."""
        # Special handling for subscript assignment
        if isinstance(stmt.target, TpySubscript):
            obj = self._gen_expr(stmt.target.obj)
            value = self._gen_expr(stmt.value)
            obj_type = self.analyzer.get_expr_type(stmt.target.obj)
            index_type = self.analyzer.get_expr_type(stmt.target.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if self._is_global_name(stmt.target.obj) else obj
            index_expr = self._gen_index_expr(subscript_obj, stmt.target.index, index_type)

            # Use module lookup for __setitem__
            methods = builtin_modules.lookup_type_method(obj_type, "__setitem__")
            if methods:
                code = methods[0].cpp.replace("{self}", subscript_obj).replace("{0}", index_expr).replace("{1}", value)
                return f"{indent}{code};\n"
            else:
                return f"{indent}{subscript_obj}[{index_expr}] = {value};\n"

        # Default: simple assignment
        target = self._gen_expr(stmt.target)
        target_type = self.analyzer.get_expr_type(stmt.target)
        value = self._gen_expr(stmt.value, target_type)
        return f"{indent}{target} = {value};\n"

    def _gen_aug_assign_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for an augmented assignment. Returns code to write."""
        # Special handling for subscript targets - use set_value() pattern
        if isinstance(stmt.target, TpySubscript):
            return self._gen_aug_assign_subscript_code(stmt, indent)

        target = self._gen_expr(stmt.target)
        target_type = self.analyzer.get_expr_type(stmt.target)
        value = self._gen_expr(stmt.value, target_type)
        value_type = self._get_resolved_type(stmt.value, target_type)

        # Special case: Int32 += BigInt should convert BigInt to Int32, then use Int32 ops
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        if isinstance(target_type, Int32Type) and isinstance(value_type, BigIntType):
            # Dereference globals before .to_int32() conversion
            if self._is_global_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_int32()"
            value_type = INT32

        # Try module system for augmented assignment (a += b is a = a + b)
        if target_type and (binop_result := builtin_modules.lookup_binop(target_type, stmt.op, value_type)):
            result = self._gen_binop_from_result(binop_result, target, value)
            return f"{indent}{target} = {result};\n"
        else:
            # Fallback for operators not in module system
            cpp_op = "/" if stmt.op == "//" else stmt.op
            return f"{indent}{target} {cpp_op}= {value};\n"

    def _gen_aug_assign_subscript_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for augmented assignment to subscript targets.

        Uses set_value(container, index, get_value(container, index) op value) pattern
        for range-checked read and write. Only supported for value type elements.
        """
        assert isinstance(stmt.target, TpySubscript)
        subscript = stmt.target
        obj = self._gen_expr(subscript.obj)
        obj_type = self._get_resolved_type(subscript.obj)
        index_type = self.analyzer.get_expr_type(subscript.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if self._is_global_name(subscript.obj) else obj
        index_expr = self._gen_index_expr(subscript_obj, subscript.index, index_type or INT32)

        # Get element type
        elem_type = obj_type.get_element_type()

        # Only allow augmented assignment on value type elements
        if elem_type and not elem_type.is_value_type():
            raise RuntimeError(
                f"Augmented assignment on container elements not supported for object types "
                f"(element type: {elem_type})"
            )

        # Generate read expression using module lookup for __getitem__
        get_methods = builtin_modules.lookup_type_method(obj_type, "__getitem__")
        if get_methods:
            read_expr = get_methods[0].cpp.replace("{self}", subscript_obj).replace("{0}", index_expr)
        else:
            read_expr = f"{subscript_obj}[{index_expr}]"

        value = self._gen_expr(stmt.value, elem_type)
        value_type = self._get_resolved_type(stmt.value, elem_type)

        # Special case: Int32 += BigInt should convert BigInt to Int32
        if isinstance(elem_type, Int32Type) and isinstance(value_type, BigIntType):
            # Dereference globals before .to_int32() conversion
            if self._is_global_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_int32()"
            value_type = INT32

        # Compute the result expression
        if elem_type and (binop_result := builtin_modules.lookup_binop(elem_type, stmt.op, value_type)):
            result_expr = self._gen_binop_from_result(binop_result, read_expr, value)
        else:
            cpp_op = "/" if stmt.op == "//" else stmt.op
            result_expr = f"{read_expr} {cpp_op} {value}"

        # Generate write using module lookup for __setitem__
        set_methods = builtin_modules.lookup_type_method(obj_type, "__setitem__")
        if set_methods:
            code = set_methods[0].cpp.replace("{self}", subscript_obj).replace("{0}", index_expr).replace("{1}", result_expr)
            return f"{indent}{code};\n"
        else:
            return f"{indent}{subscript_obj}[{index_expr}] = {result_expr};\n"

    def _gen_if(self, out: TextIO, stmt: TpyIf, indent: str) -> None:
        """Generate an if statement."""
        cond = self._gen_expr(stmt.condition)
        self._flush_pending_temps(out, indent)
        out.write(f"{indent}if ({cond}) {{\n")

        self.indent_level += 1
        for s in stmt.then_body:
            self._gen_stmt(out, s)
        self.indent_level -= 1

        if stmt.else_body:
            out.write(f"{indent}}} else {{\n")
            self.indent_level += 1
            for s in stmt.else_body:
                self._gen_stmt(out, s)
            self.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_while(self, out: TextIO, stmt: TpyWhile, indent: str) -> None:
        """Generate a while loop."""
        cond = self._gen_expr(stmt.condition)
        self._flush_pending_temps(out, indent)
        out.write(f"{indent}while ({cond}) {{\n")

        self.indent_level += 1
        for s in stmt.body:
            self._gen_stmt(out, s)
        self.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_for(self, out: TextIO, stmt: TpyFor, indent: str) -> None:
        """Generate a for loop (range-based)."""
        start = self._gen_expr_deref(stmt.start)
        end = self._gen_expr_deref(stmt.end)
        # Convert BigInt bounds to Int32 (range loops use Int32 counter)
        start_type = self.analyzer.get_expr_type(stmt.start)
        end_type = self.analyzer.get_expr_type(stmt.end)
        if isinstance(start_type, BigIntType):
            start = f"{start}.to_int32()"
        if isinstance(end_type, BigIntType):
            end = f"{end}.to_int32()"
        self._flush_pending_temps(out, indent)
        out.write(f"{indent}for (int32_t {stmt.var} = {start}; {stmt.var} < {end}; ++{stmt.var}) {{\n")

        # Track loop variable as local to prevent false global deref if it shadows a global
        self.local_scope_names.add(stmt.var)
        # Set up inner namespace for loop variable
        old_ns = self.current_ns
        if self.current_ns:
            inner_ns = Namespace(parent=self.current_ns)
            inner_ns.bind_variable(stmt.var, INT32)
            self.current_ns = inner_ns
        self.indent_level += 1
        for s in stmt.body:
            self._gen_stmt(out, s)
        self.indent_level -= 1
        self.local_scope_names.discard(stmt.var)
        self.current_ns = old_ns

        out.write(f"{indent}}}\n")

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection."""
        iterable = self._gen_expr_deref(stmt.iterable)
        iterable_type = self._get_resolved_type(stmt.iterable)

        # Determine element type for the loop variable
        elem_type = iterable_type.get_element_type()
        if elem_type is None and isinstance(iterable_type, StrType):
            elem_type = CHAR

        # Resolve IntLiteralType to BigInt (Python default for int lists)
        if isinstance(elem_type, IntLiteralType):
            elem_type = BIGINT

        # For strings, wrap in std::string_view for range-based for
        if isinstance(iterable_type, StrType):
            iterable = f"std::string_view({iterable})"

        # Flush any pending temps before for loop header
        self._flush_pending_temps(out, indent)

        # Generate C++ range-based for loop
        if elem_type:
            cpp_type = elem_type.to_cpp()
            out.write(f"{indent}for ({cpp_type} {stmt.var} : {iterable}) {{\n")
            # Track the loop variable's type for use in body expressions
            self.var_types[stmt.var] = elem_type
        else:
            # Fallback: use auto
            out.write(f"{indent}for (auto {stmt.var} : {iterable}) {{\n")

        # Track loop variable as local to prevent false global deref if it shadows a global
        self.local_scope_names.add(stmt.var)
        # Set up inner namespace for loop variable
        old_ns = self.current_ns
        if self.current_ns and elem_type:
            inner_ns = Namespace(parent=self.current_ns)
            inner_ns.bind_variable(stmt.var, elem_type)
            self.current_ns = inner_ns
        self.indent_level += 1
        for s in stmt.body:
            self._gen_stmt(out, s)
        self.indent_level -= 1
        self.local_scope_names.discard(stmt.var)
        self.current_ns = old_ns

        out.write(f"{indent}}}\n")

        # Remove loop variable type after loop ends
        if stmt.var in self.var_types:
            del self.var_types[stmt.var]

    def _is_global_name(self, expr: TpyExpr) -> bool:
        """Check if expression is a reference to a global variable.

        Returns False if the name is shadowed by a local variable or parameter.
        """
        if not isinstance(expr, TpyName):
            return False

        # Use namespace if available
        if self.current_ns:
            # Check if it's a global variable
            global_binding = self.analyzer.global_ns.lookup_local(expr.name)
            if global_binding is None or global_binding.kind != BindingKind.VARIABLE:
                return False  # Not a global variable

            # Check if locally shadowed (only if we have a local namespace, not global_ns itself)
            if self.current_ns is not self.analyzer.global_ns:
                # Traverse local namespace chain (up to but not including global_ns)
                ns = self.current_ns
                while ns is not None and ns is not self.analyzer.global_ns:
                    if ns.lookup_local(expr.name):
                        return False  # Locally shadowed
                    ns = ns.parent

            return True

        # Fallback: use old tracking
        # local_scope_names contains function params and locally-declared variables
        # (not pre-seeded globals from __tpy_init_*)
        return expr.name in self.global_names and expr.name not in self.local_scope_names

    def _is_negative_literal(self, expr: TpyExpr) -> tuple[bool, int]:
        """Check if expression is a negative integer literal.

        Returns (True, abs_value) if it's a negative literal, (False, 0) otherwise.
        """
        if isinstance(expr, TpyUnaryOp) and expr.op == "-":
            if isinstance(expr.operand, TpyIntLiteral):
                return (True, expr.operand.value)
        return (False, 0)

    def _gen_index_expr(self, obj: str, index: TpyExpr, index_type: TpyType) -> str:
        """Generate index expression, handling negative indices with Python semantics."""
        is_neg, abs_val = self._is_negative_literal(index)
        if is_neg:
            # Negative literal: items[-1] -> items[tpy::__len__(items) - 1]
            # Use tpy::__len__() for universal support (std types and user records)
            return f"static_cast<int32_t>(tpy::__len__({obj}) - {abs_val})"

        index_expr = self._gen_expr_deref(index)
        if self._is_runtime_bigint(index, index_type):
            index_expr = f"{index_expr}.to_int32()"
        return index_expr

    def _gen_method_from_def(self, obj: str, args: list[TpyExpr],
                             method: builtin_modules.MethodDef) -> str:
        """Generate method call code from a MethodDef.

        Substitutes {self} with obj and {0}, {1}, etc. with generated args.
        """
        result = method.cpp.replace("{self}", obj)
        for i, arg in enumerate(args):
            result = result.replace(f"{{{i}}}", self._gen_expr(arg))
        return result

    def _gen_binop_from_result(self, binop_result: builtin_modules.BinopResult,
                               left: str, right: str) -> str:
        """Generate binary operation code from a BinopResult.

        Applies wrappers to operands and substitutes into the method template.
        Handles is_reverse flag for reverse operators (__radd__, etc.).
        """
        wrapped_left = binop_result.left_wrapper.replace("{self}", left).replace("{expr}", left)
        wrapped_right = binop_result.right_wrapper.replace("{self}", right).replace("{expr}", right)
        if binop_result.is_reverse:
            return binop_result.method.cpp.replace("{self}", wrapped_right).replace("{0}", wrapped_left)
        else:
            return binop_result.method.cpp.replace("{self}", wrapped_left).replace("{0}", wrapped_right)

    def _gen_expr_deref(self, expr: TpyExpr, target_type: TpyType = None) -> str:
        """Generate an expression, dereferencing globals.

        Use this when the underlying value is needed (e.g., method calls,
        operators, function arguments). For assignment targets, use _gen_expr.
        """
        result = self._gen_expr(expr, target_type)
        if self._is_global_name(expr):
            result = f"(*{result})"
        return result

    def _gen_expr(self, expr: TpyExpr, target_type: TpyType = None) -> str:
        """Generate an expression.

        Args:
            expr: The expression to generate
            target_type: Optional expected type (for implicit promotion)
        """
        if isinstance(expr, TpyIntLiteral):
            # Promote to BigInt if target expects it
            if isinstance(target_type, BigIntType):
                return f"tpy::BigInt({expr.value})"
            return str(expr.value)

        elif isinstance(expr, TpyFloatLiteral):
            # C++ accepts Python-style float literals directly
            return repr(expr.value)

        elif isinstance(expr, TpyBoolLiteral):
            return "true" if expr.value else "false"

        elif isinstance(expr, TpyCoerce):
            if expr.coercion.name == "int_literal_to_int32" or isinstance(expr.expected_type, SpanType):
                inner_target = expr.expected_type
            else:
                inner_target = expr.actual_type
            gen_inner = self._gen_expr(expr.expr, inner_target)
            if isinstance(expr.expected_type, SpanType):
                return self._gen_span_coercion(expr.expr, expr.expected_type, gen_inner)
            # IntLiteralType may be runtime BigInt; sema records this on the coercion.
            if expr.coercion.name == "int_literal_to_int32":
                if expr.runtime_bigint:
                    return f"({gen_inner}).to_int32()"
                return gen_inner
            # Coercions that call methods on the inner expression need dereferencing for globals
            if expr.coercion.name in ("record_to_ptr", "record_to_const_ptr", "bigint_to_int32"):
                if self._is_global_name(expr.expr):
                    gen_inner = f"(*{gen_inner})"
            return expr.coercion.codegen(gen_inner, expr.actual_type, expr.expected_type, expr.context_kind)

        elif isinstance(expr, TpyStrLiteral):
            # If target type is Char and single char, output as char literal
            if isinstance(target_type, CharType) and len(expr.value) == 1:
                escaped = expr.value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                return f"'{escaped}'"
            # Otherwise output as string literal
            escaped = expr.value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            return f'"{escaped}"'

        elif isinstance(expr, TpyName):
            return expr.name

        elif isinstance(expr, TpyBinOp):
            # First pass: get raw types to detect Int32 operands
            left_raw = self._get_resolved_type(expr.left)
            right_raw = self._get_resolved_type(expr.right)
            # If one operand is Int32, resolve literals as Int32 (not BigInt)
            int32_context = target_type if isinstance(target_type, Int32Type) else None
            if isinstance(left_raw, Int32Type) or isinstance(right_raw, Int32Type):
                int32_context = INT32
            left_type = self._get_resolved_type(expr.left, int32_context)
            right_type = self._get_resolved_type(expr.right, int32_context)

            # Handle 'in' and 'not in' operators
            if expr.op in ("in", "not in"):
                left = self._gen_expr(expr.left)
                right = self._gen_expr(expr.right)
                # Dereference globals for .begin()/.end() calls
                if self._is_global_name(expr.right):
                    right = f"(*{right})"
                right_resolved = self._get_resolved_type(expr.right)
                if isinstance(right_resolved, StrType):
                    # String contains: use std::string_view::find
                    find_expr = f"(std::string_view({right}).find({left}) != std::string_view::npos)"
                else:
                    # Collection: use std::find
                    find_expr = f"(std::find({right}.begin(), {right}.end(), {left}) != {right}.end())"
                if expr.op == "not in":
                    return f"(!{find_expr})"
                return find_expr

            # Comparison operators - generate C++ directly
            if expr.op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                # When comparing Char with string literal, output literal as char
                left_target = CHAR if isinstance(right_type, CharType) else None
                right_target = CHAR if isinstance(left_type, CharType) else None
                # Use _gen_expr_deref for globals (Global<T> needs dereferencing for comparison)
                left = self._gen_expr_deref(expr.left, left_target)
                right = self._gen_expr_deref(expr.right, right_target)
                return f"({left} {expr.op} {right})"

            # Optimization: IntLiteral op IntLiteral with Int32 target → direct Int32 arithmetic
            # This avoids unnecessary BigInt heap allocations
            # Use analyzer types for this check - analyzer returns IntLiteralType for all-literal
            # expressions (including nested binops like 2+3), while _get_resolved_type returns BigInt
            # NOTE: Must also check operands aren't variables (loop vars have IntLiteralType but aren't literals)
            left_analyzer_type = self.analyzer.get_expr_type(expr.left)
            right_analyzer_type = self.analyzer.get_expr_type(expr.right)
            left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
            right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)
            if (isinstance(target_type, Int32Type) and left_is_literal and right_is_literal):
                # Pass target_type to handle nested binops like 1 + (2 + 3)
                left = self._gen_expr(expr.left, target_type)
                right = self._gen_expr(expr.right, target_type)
                # Use module system to get Int32 binary operator
                method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
                if method_name:
                    methods = builtin_modules.lookup_type_method(INT32, method_name)
                    if method := builtin_modules._find_matching_overload(methods, INT32):
                        result = method.cpp.replace("{self}", left).replace("{0}", right)
                        return result
                # Fallback for operators not in module system (bitwise operators)
                return f"({left} {expr.op} {right})"

            # Try module system for arithmetic/bitwise operators
            if binop_result := builtin_modules.lookup_binop(left_type, expr.op, right_type):
                # Get types for proper literal promotion
                param_type = binop_result.method.params[0].type if binop_result.method.params else None
                receiver_type = binop_result.receiver_type
                # For reverse operators, {self} is the right operand, {0} is left
                # For forward operators, {self} is the left operand, {0} is right
                if binop_result.is_reverse:
                    # right is {self} (receiver), left is {0} (argument)
                    left = self._gen_expr(expr.left, param_type)
                    right = self._gen_expr(expr.right, receiver_type)
                    # Dereference globals BEFORE conversion (tpy::Global<T> needs explicit deref)
                    if self._is_global_name(expr.left):
                        left = f"(*{left})"
                    if self._is_global_name(expr.right):
                        right = f"(*{right})"
                    # Convert argument if needed (e.g., IntLiteralType that's actually BigInt)
                    left = self._convert_to_int32_arg(left, left_type, param_type, expr.left)
                else:
                    # left is {self} (receiver), right is {0} (argument)
                    left = self._gen_expr(expr.left, receiver_type)
                    right = self._gen_expr(expr.right, param_type)
                    # Dereference globals BEFORE conversion (tpy::Global<T> needs explicit deref)
                    if self._is_global_name(expr.left):
                        left = f"(*{left})"
                    if self._is_global_name(expr.right):
                        right = f"(*{right})"
                    # Convert argument if needed (e.g., IntLiteralType that's actually BigInt)
                    right = self._convert_to_int32_arg(right, right_type, param_type, expr.right)
                # Generate binop using helper (handles wrappers and is_reverse)
                result = self._gen_binop_from_result(binop_result, left, right)
                # Wrap in parens to avoid precedence issues with cout << and other operators
                return f"({result})"

            # Fallback for IntLiteral + IntLiteral → BigInt (arbitrary precision)
            # (Int32 case is handled earlier as an optimization)
            if isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType):
                left = self._gen_expr(expr.left, BIGINT)
                right = self._gen_expr(expr.right, BIGINT)
                cpp_op = "/" if expr.op == "//" else expr.op
                return f"({left} {cpp_op} {right})"

            # Protocol-typed operands - use C++ operator syntax
            # The protocol constraint guarantees the operator exists
            if isinstance(left_type, ProtocolType):
                left = self._gen_expr(expr.left, left_type)
                right = self._gen_expr(expr.right, right_type)
                # Map Python operators to C++ operators
                cpp_op = expr.op
                if expr.op == "//":
                    cpp_op = "/"  # Floor division maps to / in C++
                return f"({left} {cpp_op} {right})"

            # User-defined types (RecordType) - use generated C++ operator
            if isinstance(left_type, RecordType):
                left = self._gen_expr(expr.left, left_type)
                right = self._gen_expr(expr.right, right_type)
                # Map Python operators to C++ operators
                cpp_op = expr.op
                if expr.op == "//":
                    cpp_op = "/"  # Floor division maps to / in C++
                return f"({left} {cpp_op} {right})"

            raise RuntimeError(f"No codegen for binary operator {expr.op} with {left_type} and {right_type}")

        elif isinstance(expr, TpyUnaryOp):
            operand = self._gen_expr(expr.operand, target_type)
            operand_type = self.analyzer.get_expr_type(expr.operand)

            # Logical not
            if expr.op == "!":
                return f"(!{operand})"

            # Dereference globals for unary operations
            if self._is_global_name(expr.operand):
                operand = f"(*{operand})"

            # Try module system for unary operators
            if unaryop_result := builtin_modules.lookup_unaryop(operand_type, expr.op):
                return unaryop_result.method.cpp.format(self=operand)

            # Fallback for IntLiteralType (not in module system)
            if isinstance(operand_type, IntLiteralType):
                return f"({expr.op}{operand})"

            raise RuntimeError(f"No codegen for unary operator {expr.op} with {operand_type}")

        elif isinstance(expr, TpyCall):
            # Check if it's a builtin type constructor (e.g., Int32, int)
            if type_def := builtin_modules.lookup_type_by_func_name(expr.func):
                return self._gen_constructor(expr, type_def)
            # print() maps to std::printf
            if expr.func == "print":
                return self._gen_print(expr.args, expr.kwargs)
            # Check module registry for built-in functions
            if builtin_fn := builtin_modules.lookup_function(expr.func):
                return self._gen_builtin_call(expr, builtin_fn)
            # Check for imported function (from X import Y -> Y())
            # Only if not shadowed by a variable, user-defined function, or record
            if expr.func in self.analyzer.imported_names:
                is_shadowed = (expr.func in self.declared_vars or
                               expr.func in self.global_names or
                               self.analyzer.registry.get_function(expr.func) is not None or
                               self.analyzer.registry.get_record(expr.func) is not None)
                if not is_shadowed:
                    module_name, func_name = self.analyzer.imported_names[expr.func]
                    if imported_fn := builtin_modules.lookup_module_function(module_name, func_name):
                        return self._gen_builtin_call(expr, imported_fn)
            # Check if this is a function call that needs argument conversion
            func_info = self.analyzer.registry.get_function(expr.func)
            if func_info:
                gen_args = []
                for arg, (pname, ptype) in zip(expr.args, func_info.params):
                    # Temporaries passed to mutable reference params need a temp variable
                    # because C++ can't bind rvalue to non-const lvalue reference
                    if ptype.is_ref_param() and self._is_temporary_expr(arg):
                        init_expr = self._gen_expr(arg, ptype)
                        temp_name = self._create_temp_for_literal(ptype, init_expr)
                        gen_args.append(temp_name)
                    else:
                        # Pass param type for BigInt promotion
                        gen_arg = self._gen_expr(arg, ptype)
                        gen_args.append(gen_arg)
                return f"{expr.func}({', '.join(gen_args)})"
            # Generic type instantiation (e.g., StaticList[T, N]())
            if expr.call_type is not None:
                # Special case: StaticList[T,N]([x]*count) - list repeat already generates
                # the fill constructor, so just return it directly (avoid redundant copy)
                if (isinstance(expr.call_type, StaticListType) and
                    len(expr.args) == 1 and isinstance(expr.args[0], TpyListRepeat)):
                    return self._gen_expr(expr.args[0], expr.call_type)
                # Check for constructor with cpp template (e.g., list(iterable))
                # Skip for literals - they use simpler initialization
                if expr.args and not isinstance(expr.args[0], TpyArrayLiteral):
                    if lookup := builtin_modules.lookup_generic_type(expr.func):
                        type_def = lookup.type_def
                        if type_def.constructors:
                            # Find matching constructor and use its cpp template
                            arg_types = [self._get_resolved_type(a) for a in expr.args]
                            for ctor in type_def.constructors:
                                if len(ctor.params) == len(arg_types):
                                    # Check if this constructor matches (Iterable matches containers)
                                    if all(self._ctor_param_matches(at, p.type) for at, p in zip(arg_types, ctor.params)):
                                        type_params = builtin_modules.extract_type_params(expr.call_type)
                                        # Dereference globals for constructor templates that use method calls
                                        gen_args = []
                                        for a in expr.args:
                                            gen = self._gen_expr(a, expr.call_type)
                                            if self._is_global_name(a):
                                                gen = f"(*{gen})"
                                            gen_args.append(gen)
                                        return self._apply_cpp_template(ctor.cpp, gen_args, type_params, expr.call_type)
                # Pass call_type as target for proper nested array brace generation
                args = ", ".join(self._gen_expr(a, expr.call_type) for a in expr.args)
                return f"{expr.call_type.to_cpp()}({args})"
            args = ", ".join(self._gen_expr(a) for a in expr.args)
            return f"{expr.func}({args})"

        elif isinstance(expr, TpyMethodCall):
            args = ", ".join(self._gen_expr(a) for a in expr.args)
            # Handle self.method() -> just method() (inside method, implicit this)
            if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
                return f"{expr.method}({args})"
            # Handle module.function() (import X -> X.func())
            # Only if the name isn't shadowed by a variable, user-defined function, or record
            if isinstance(expr.obj, TpyName) and expr.obj.name in self.analyzer.imports:
                module_name = expr.obj.name
                # Check if shadowed by variable, user-defined function, or record
                is_shadowed = (module_name in self.declared_vars or
                               module_name in self.global_names or
                               self.analyzer.registry.get_function(module_name) is not None or
                               self.analyzer.registry.get_record(module_name) is not None)
                if not is_shadowed:
                    if self.analyzer.imports[module_name] is None:
                        if module_fn := builtin_modules.lookup_module_function(module_name, expr.method):
                            # Create a temp call for code generation
                            temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                            return self._gen_builtin_call(temp_call, module_fn)
            obj = self._gen_expr(expr.obj)
            obj_type = self._get_resolved_type(expr.obj)

            # Try module lookup for methods on builtin types
            methods = builtin_modules.lookup_type_method(obj_type, expr.method)
            if methods:
                # Globals need dereferencing for method template access
                method_obj = f"(*{obj})" if self._is_global_name(expr.obj) else obj
                return self._gen_method_from_def(method_obj, expr.args, methods[0])

            # Use -> for globals (wrapped in tpy::Global<T>)
            accessor = "->" if self._is_global_name(expr.obj) else "."
            return f"{obj}{accessor}{expr.method}({args})"

        elif isinstance(expr, TpyFieldAccess):
            # Handle self.field -> just field (inside method, implicit this)
            if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
                return expr.field

            # Check for module variable access (e.g., sys.argv)
            if isinstance(expr.obj, TpyName):
                if self.current_ns:
                    binding = self.current_ns.lookup(expr.obj.name)
                    if binding and binding.kind == BindingKind.MODULE:
                        module_name = expr.obj.name
                        if module_var := builtin_modules.lookup_module_var(module_name, expr.field):
                            return module_var.cpp

            obj = self._gen_expr(expr.obj)
            # Check if obj is a pointer type or global - use -> instead of .
            obj_type = self.analyzer.get_expr_type(expr.obj)
            is_global = self._is_global_name(expr.obj)
            if obj_type and obj_type.is_pointer():
                # Global pointer needs deref first: Global<Ptr<T>> -> (*global)->field
                if is_global:
                    return f"(*{obj})->{expr.field}"
                return f"{obj}->{expr.field}"
            if is_global:
                return f"{obj}->{expr.field}"
            return f"{obj}.{expr.field}"

        elif isinstance(expr, TpyArrayLiteral):
            # Some types need explicit element targeting (Array, Span)
            # Others handle implicit conversions (List, StaticList)
            elem_target = None
            if target_type and target_type.needs_explicit_element_target():
                elem_target = target_type.get_element_type()
            elements = ", ".join(self._gen_expr(e, elem_target) for e in expr.elements)
            literal = f"{{{elements}}}"
            # std::array of std::array needs an extra brace level
            if isinstance(elem_target, ArrayType):
                return f"{{{literal}}}"
            # Empty list needs explicit type to avoid ambiguity with Global<T> assignment
            if not expr.elements and target_type and target_type.get_element_type() is not None:
                return f"{target_type.to_cpp()}{literal}"
            return literal

        elif isinstance(expr, TpyListRepeat):
            # [elements...] * N -> repeated sequence
            # Note: Empty list repetition [] * N is collapsed to [] in the parser

            count = self._gen_expr_deref(expr.count)
            count_type = self.analyzer.get_expr_type(expr.count)
            # BigInt count needs conversion (IntLiteralType is already plain int)
            if isinstance(count_type, BigIntType):
                count = f"{count}.to_int32()"

            # Determine result type and element type
            if isinstance(target_type, StaticListType):
                result_type = target_type
                elem_type = target_type.element_type
            elif isinstance(target_type, ListType):
                # Resolve IntLiteralType to BigInt
                if isinstance(target_type.element_type, IntLiteralType):
                    result_type = ListType(BIGINT)
                    elem_type = BIGINT
                else:
                    result_type = target_type
                    elem_type = target_type.element_type
            else:
                result_type = self.analyzer.get_expr_type(expr)
                # Resolve IntLiteralType element to BigInt (Python semantics)
                if isinstance(result_type, ListType) and isinstance(result_type.element_type, IntLiteralType):
                    result_type = ListType(BIGINT)
                elem_type = result_type.element_type if isinstance(result_type, ListType) else None

            # Single element: use fill constructor (more efficient)
            # Pass elem_type for proper coercion (e.g., str->char for StaticList[Char])
            # Clamp negative counts to 0 (Python semantics) - fill constructors don't handle negative
            if len(expr.elements) == 1:
                element = self._gen_expr(expr.elements[0], elem_type)
                clamped_count = f"std::max(0, {count})"
                return f"{result_type.to_cpp()}({clamped_count}, {element})"

            # Multiple elements: use tpy::repeat_range (handles negative counts internally)
            elements = ", ".join(self._gen_expr(e, elem_type) for e in expr.elements)
            cpp_elem_type = elem_type.to_cpp() if elem_type else "auto"
            range_expr = f"tpy::repeat_range<{cpp_elem_type}>({count}, {{{elements}}})"

            # Use type's range construction method
            from_range = result_type.to_cpp_from_range(range_expr, cpp_elem_type)
            if from_range:
                return from_range
            # Fallback for types without range constructor
            return f"tpy::to_vector<{cpp_elem_type}>({range_expr})"

        elif isinstance(expr, TpySubscript):
            obj = self._gen_expr(expr.obj)
            obj_type = self._get_resolved_type(expr.obj)
            index_type = self.analyzer.get_expr_type(expr.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if self._is_global_name(expr.obj) else obj
            index_expr = self._gen_index_expr(subscript_obj, expr.index, index_type)

            # Use module lookup for __getitem__
            methods = builtin_modules.lookup_type_method(obj_type, "__getitem__")
            if methods:
                return methods[0].cpp.replace("{self}", subscript_obj).replace("{0}", index_expr)
            # Fallback for types without __getitem__ (e.g., str)
            return f"{subscript_obj}[{index_expr}]"

        return "/* unknown expr */"

    def _involves_variables(self, expr: TpyExpr) -> bool:
        """Check if an expression involves any variable references."""
        if isinstance(expr, TpyCoerce):
            return self._involves_variables(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return self._involves_variables(expr.left) or self._involves_variables(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self._involves_variables(expr.operand)
        if isinstance(expr, TpyCall):
            return True  # Function calls may return BigInt
        if isinstance(expr, TpyMethodCall):
            return True
        # Default to True for safety
        return True

    def _is_int32_arithmetic(self, left_type: TpyType, right_type: TpyType, op: str) -> bool:
        """Check if binary op produces Int32 result (needs checked arithmetic).

        Only applies when at least one operand is explicitly Int32Type.
        IntLiteralType alone defaults to BigInt (Python semantics).
        """
        if op not in ("+", "-", "*", "//", "%", "**"):
            return False
        # Need at least one explicit Int32 operand
        has_int32 = isinstance(left_type, Int32Type) or isinstance(right_type, Int32Type)
        if not has_int32:
            return False
        # The other operand must be Int32 or IntLiteral (coerces to Int32)
        def is_int32_compatible(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, IntLiteralType))
        return is_int32_compatible(left_type) and is_int32_compatible(right_type)

    def _is_runtime_bigint(self, expr: TpyExpr, expr_type: TpyType) -> bool:
        """Check if expression is stored as BigInt at runtime."""
        if isinstance(expr_type, BigIntType):
            return True
        if isinstance(expr_type, IntLiteralType):
            return self._involves_variables(expr)
        return False

    def _gen_overloaded_call(self, args: list[TpyExpr], overloads: list[builtin_modules.MethodDef], name: str) -> str:
        """Generate C++ for an overloaded call (constructor or builtin function).

        Finds matching overload, generates args with proper type coercion, substitutes into template.
        """
        arg_types = [self.analyzer.get_expr_type(arg) for arg in args]

        for overload in overloads:
            if len(overload.params) != len(args):
                continue
            if all(self._builtin_codegen_type_matches(arg, arg_t, param.type)
                   for arg, arg_t, param in zip(args, arg_types, overload.params)):
                # Generate args with proper type coercion (e.g., int literal → BigInt)
                # Use _gen_expr_deref to handle globals (tpy::Global<T> needs dereferencing)
                gen_args = [self._gen_expr_deref(arg, param.type) for arg, param in zip(args, overload.params)]
                return overload.cpp.format(*gen_args)

        raise RuntimeError(f"No matching overload for {name}")

    def _gen_constructor(self, expr: TpyCall, type_def: builtin_modules.BuiltinTypeDef) -> str:
        """Generate C++ code for a type constructor call."""
        return self._gen_overloaded_call(expr.args, type_def.constructors, expr.func)

    def _ctor_param_matches(self, arg_type: TpyType, param_type: builtin_modules.TypeOrParam) -> bool:
        """Check if argument type matches constructor parameter (for generic type constructors)."""
        if param_type == "Iterable":
            return arg_type.is_iterable()
        if isinstance(param_type, TpyType):
            return arg_type == param_type or (
                isinstance(arg_type, IntLiteralType) and isinstance(param_type, (Int32Type, BigIntType))
            )
        return False

    def _apply_cpp_template(
        self, template: str, args: list[str], type_params: dict[str, TpyType], result_type: TpyType
    ) -> str:
        """Apply a cpp template with argument and type parameter substitution."""
        result = template
        # Substitute positional arguments {0}, {1}, etc.
        for i, arg in enumerate(args):
            result = result.replace(f"{{{i}}}", arg)
        # Substitute type parameters {T}, etc.
        for name, typ in type_params.items():
            result = result.replace(f"{{{name}}}", typ.to_cpp())
        return result

    def _gen_builtin_call(self, expr: TpyCall, fn_def: builtin_modules.BuiltinFunctionDef) -> str:
        """Generate C++ code for a built-in function call from the module registry."""
        return self._gen_overloaded_call(expr.args, fn_def.overloads, fn_def.name)

    def _builtin_codegen_type_matches(self, arg: TpyExpr, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument matches a parameter type for codegen purposes."""
        # Direct type match
        if isinstance(arg_type, type(param_type)) and arg_type == param_type:
            return True
        # Protocol parameter: sema already verified conformance, accept any arg
        if isinstance(param_type, ProtocolType):
            return True
        # IntLiteral can match Int32 (if compile-time) or BigInt (if runtime)
        if isinstance(arg_type, IntLiteralType):
            if isinstance(param_type, BigIntType):
                return self._is_runtime_bigint(arg, arg_type)
            if isinstance(param_type, Int32Type):
                return not self._is_runtime_bigint(arg, arg_type)
        # TpyCoerce nodes match their expected type
        if isinstance(arg, TpyCoerce):
            return arg.expected_type == param_type
        return False

    def _gen_span_coercion(self, expr: TpyExpr, span_type: SpanType, gen_inner: str) -> str:
        """Generate std::span conversion for supported container types."""
        if isinstance(expr, TpyArrayLiteral):
            expected_array_type = ArrayType(span_type.element_type, len(expr.elements))
            array_expr = f"{expected_array_type.to_cpp()}{gen_inner}"
            return f"tpy::as_span({array_expr})"
        # gen_inner already generated, need to check if source was global
        if self._is_global_name(expr):
            gen_inner = f"(*{gen_inner})"
        return f"tpy::as_span({gen_inner})"

    def _convert_to_int32_arg(self, gen_expr: str, actual_type: TpyType, expected_type: TpyType, expr: TpyExpr) -> str:
        """Convert to Int32 when a runtime BigInt may be present."""
        if isinstance(expected_type, Int32Type):
            if isinstance(actual_type, BigIntType):
                return f"({gen_expr}).to_int32()"
            if isinstance(actual_type, IntLiteralType) and self._is_runtime_bigint(expr, actual_type):
                return f"({gen_expr}).to_int32()"
        return gen_expr

    def _gen_print(self, args: list[TpyExpr], kwargs: dict[str, TpyExpr] = None) -> str:
        """Generate std::cout call for print()."""
        kwargs = kwargs or {}

        # Determine line ending (default is newline)
        end_str = "\\n"
        if "end" in kwargs:
            end_expr = kwargs["end"]
            if isinstance(end_expr, TpyStrLiteral):
                end_str = end_expr.value.replace('\\', '\\\\').replace('"', '\\"')

        if not args:
            if end_str:
                return f'std::cout << "{end_str}"'
            return ""

        parts = []
        for i, arg in enumerate(args):
            if i > 0:
                parts.append('" "')  # Space separator between args

            arg_type = self._get_resolved_type(arg)

            if isinstance(arg, TpyStrLiteral):
                escaped = arg.value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                parts.append(f'"{escaped}"')
            elif self._is_runtime_bigint(arg, arg_type):
                # BigInt has operator<< for std::ostream, no .to_string() needed
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, FloatType):
                # Float uses Python-style formatting via tpy::print_float
                parts.append(f'tpy::print_float({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, BoolType):
                # Bool uses Python-style formatting via tpy::print_bool
                parts.append(f'tpy::print_bool({self._gen_expr_deref(arg)})')
            elif arg_type.get_element_type() is not None:
                # Container types use ListPrinter for formatting
                if isinstance(arg, TpyArrayLiteral):
                    # Array literals need explicit type for ListPrinter CTAD
                    cpp_type = arg_type.to_cpp()
                    parts.append(f'tpy::ListPrinter({cpp_type}{self._gen_expr(arg)})')
                else:
                    parts.append(f'tpy::ListPrinter({self._gen_expr_deref(arg)})')
            else:
                # Int32, Char, Bool, literals, etc. - direct output
                parts.append(self._gen_expr_deref(arg))

        # Add end string
        if end_str:
            parts.append(f'"{end_str}"')

        return "std::cout << " + " << ".join(parts)
