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
    TpyType, Int32Type, VoidType, RecordType, PtrType, ConstPtrType,
    StaticListType, ArrayType, SpanType, ListType, PendingListType,
    StrType, CharType, BigIntType, IntLiteralType,
    INT32, VOID, BIGINT, CHAR
)
from .parse import (
    SourceLocation,
    TpyModule, TpyRecord, TpyFunction, TpyStmt, TpyExpr,
    TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn, TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue,
    TpyIntLiteral, TpyStrLiteral, TpyBoolLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript
)
from .sema import SemanticAnalyzer


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
        self.source_lines: list[str] = []  # Source lines for emit_source_comments

    def _get_resolved_type(self, expr: TpyExpr) -> TpyType:
        """Get the resolved type of an expression, handling PendingListType.

        PendingListType is used during semantic analysis but should be resolved
        to concrete Array or list types before codegen. This method looks up
        the resolved type if needed.
        """
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
        # (needed for extern declarations in header before records)
        global_decls = []
        main_stmts = []
        for stmt in module.top_level_stmts:
            # Top-level variable declarations with explicit type are globals
            # Also treat unannotated list literals as globals (for list inference)
            is_global = False
            if isinstance(stmt, TpyVarDecl):
                if stmt.type:
                    is_global = True
                elif isinstance(stmt.init, TpyArrayLiteral):
                    # Unannotated list literal at top level -> global
                    is_global = True
            if is_global:
                global_decls.append(stmt)
            else:
                main_stmts.append(stmt)

        # Generate extern declarations in header (before records so methods can see them)
        if global_decls:
            for stmt in global_decls:
                self._gen_global_extern(hpp, stmt)
            hpp.write("\n")

        # Generate records
        for record in module.records:
            self._gen_record_decl(hpp, record)
            hpp.write("\n")

        # Generate function declarations
        for func in module.functions:
            self._gen_function_decl(hpp, func)
        hpp.write("\n")

        # Generate global definitions in source (before functions)
        if global_decls:
            for stmt in global_decls:
                self._gen_global_decl(cpp, stmt)
            cpp.write("\n")

        # Generate function definitions
        for func in module.functions:
            self._gen_function_def(cpp, func)
            cpp.write("\n")

        # Generate module init function and main()
        # Skip if user already defined a main() function
        has_user_main = any(f.name == "main" for f in module.functions)
        if not has_user_main:
            # Always generate init function (even if empty, for consistency)
            self._gen_module_init_decl(hpp)
            self._gen_module_init(cpp, main_stmts)
            self._gen_main(cpp)

        self._write_header_epilogue(hpp)

        return hpp.getvalue(), cpp.getvalue()

    def _write_header_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write("#pragma once\n\n")
        out.write('#include "tpy_runtime.hpp"\n\n')

    def _write_source_preamble(self, out: TextIO) -> None:
        out.write("// Generated by TurboPython Compiler\n")
        out.write(f'#include "{self.module_name}.hpp"\n\n')

    def _write_header_epilogue(self, out: TextIO) -> None:
        pass

    def _gen_module_init_decl(self, out: TextIO) -> None:
        """Generate module init function declaration in header."""
        out.write(f"void __tpy_init_{self.module_name}();\n")

    def _gen_module_init(self, out: TextIO, stmts: list) -> None:
        """Generate module init function containing top-level statements."""
        out.write(f"void __tpy_init_{self.module_name}() {{\n")
        self.declared_vars = set()
        self.var_types = {}
        self.indent_level = 1
        for stmt in stmts:
            self._gen_stmt(out, stmt)
        out.write("}\n\n")

    def _gen_main(self, out: TextIO) -> None:
        """Generate C++ main() that calls module init."""
        out.write("int main() {\n")
        out.write(f"  __tpy_init_{self.module_name}();\n")
        out.write("  return 0;\n")
        out.write("}\n")

    def _gen_global_decl(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a global variable definition in source file."""
        self._emit_source_comment(out, stmt.loc)
        # Use explicit type if provided, otherwise infer from initializer
        if stmt.type:
            var_type = stmt.type
        elif stmt.init:
            var_type = self._get_resolved_type(stmt.init)
        else:
            raise RuntimeError(f"Global '{stmt.name}' has no type and no initializer")
        cpp_type = var_type.to_cpp()
        if stmt.init:
            init_expr = self._gen_expr(stmt.init, var_type)
            out.write(f"{cpp_type} {stmt.name} = {init_expr};\n")
        else:
            out.write(f"{cpp_type} {stmt.name};\n")

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
        out.write(f"extern {cpp_type} {stmt.name};\n")

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
                params = ", ".join(
                    f"{ptype.to_cpp()} {pname}"
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
                    self.indent_level = 2
                    self.in_method = True
                    for stmt in non_init_stmts:
                        self._gen_stmt(out, stmt)
                    self.in_method = False
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
                    self.indent_level = 2
                    self.in_method = True
                    for stmt in non_init_stmts:
                        self._gen_stmt(out, stmt)
                    self.in_method = False
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
            self._gen_method(out, method)

        out.write("};\n")

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

    def _gen_method(self, out: TextIO, method: TpyFunction) -> None:
        """Generate a method definition inside a struct."""
        ret_type = method.return_type.to_cpp()
        params = self._gen_params(method.params)
        out.write(f"\n  {ret_type} {method.name}({params}) {{\n")

        # Reset declared vars and add parameters
        self.declared_vars = {pname for pname, _ in method.params}
        self.var_types = {pname: ptype for pname, ptype in method.params}
        self.indent_level = 2
        self.in_method = True
        self.current_return_type = method.return_type
        self.current_func_params = {pname: ptype for pname, ptype in method.params}
        for stmt in method.body:
            self._gen_stmt(out, stmt)
        self.in_method = False
        self.indent_level = 0

        out.write("  }\n")

    def _gen_function_decl(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function declaration."""
        ret_type = func.return_type.to_cpp()
        # Special case: main() must return int
        if func.name == "main":
            ret_type = "int"
        params = self._gen_params(func.params)
        out.write(f"{ret_type} {func.name}({params});\n")

    def _gen_function_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function definition."""
        self._emit_source_comment(out, func.loc)

        ret_type = func.return_type.to_cpp()
        # Special case: main() must return int
        if func.name == "main":
            ret_type = "int"
        params = self._gen_params(func.params)
        out.write(f"{ret_type} {func.name}({params}) {{\n")

        # Reset declared vars and add parameters
        self.declared_vars = {pname for pname, _ in func.params}
        self.var_types = {pname: ptype for pname, ptype in func.params}
        self.indent_level = 1
        self.current_return_type = func.return_type
        self.current_func_params = {pname: ptype for pname, ptype in func.params}
        for stmt in func.body:
            self._gen_stmt(out, stmt)

        # Add return 0 for main() if not already returning
        if func.name == "main" and isinstance(func.return_type, VoidType):
            out.write("  return 0;\n")
        self.indent_level = 0

        out.write("}\n")

    def _gen_params(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate function parameter list."""
        parts = []
        for pname, ptype in params:
            # SpanType and StrType are lightweight views, pass by value
            if isinstance(ptype, (SpanType, StrType)):
                parts.append(f"{ptype.to_cpp()} {pname}")
            # Pass StaticList, Array, List, and Record types by reference
            elif isinstance(ptype, (StaticListType, ArrayType, ListType, RecordType)):
                parts.append(f"{ptype.to_cpp()}& {pname}")
            # BigInt is passed by const reference for efficiency
            elif isinstance(ptype, BigIntType):
                parts.append(f"const {ptype.to_cpp()}& {pname}")
            else:
                parts.append(f"{ptype.to_cpp()} {pname}")
        return ", ".join(parts)

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

    def _gen_stmt(self, out: TextIO, stmt: TpyStmt) -> None:
        """Generate a statement."""
        indent = "  " * self.indent_level

        self._emit_source_comment(out, stmt.loc, indent)

        if isinstance(stmt, TpyVarDecl):
            self._gen_var_decl(out, stmt, indent)
        elif isinstance(stmt, TpyAssign):
            self._gen_assign(out, stmt, indent)
        elif isinstance(stmt, TpyAugAssign):
            self._gen_aug_assign(out, stmt, indent)
        elif isinstance(stmt, TpyExprStmt):
            # Skip docstrings (string literal expression statements)
            if isinstance(stmt.expr, TpyStrLiteral):
                return
            expr = self._gen_expr(stmt.expr)
            out.write(f"{indent}{expr};\n")
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                # Pass return type for BigInt promotion
                ret_type = self.current_return_type if hasattr(self, 'current_return_type') else None
                value_type = self._get_resolved_type(stmt.value)
                expr = self._gen_expr(stmt.value, ret_type)
                # Convert BigInt -> Int32 if needed
                if ret_type and isinstance(ret_type, Int32Type) and self._is_runtime_bigint(stmt.value, value_type):
                    expr = f"({expr}).to_int32()"
                out.write(f"{indent}return {expr};\n")
            else:
                out.write(f"{indent}return;\n")
        elif isinstance(stmt, TpyIf):
            self._gen_if(out, stmt, indent)
        elif isinstance(stmt, TpyWhile):
            self._gen_while(out, stmt, indent)
        elif isinstance(stmt, TpyFor):
            self._gen_for(out, stmt, indent)
        elif isinstance(stmt, TpyForEach):
            self._gen_for_each(out, stmt, indent)
        elif isinstance(stmt, TpyBreak):
            out.write(f"{indent}break;\n")
        elif isinstance(stmt, TpyContinue):
            out.write(f"{indent}continue;\n")

    def _gen_var_decl(self, out: TextIO, stmt: TpyVarDecl, indent: str) -> None:
        """Generate a variable declaration or assignment."""
        # Check if variable is already declared (reassignment)
        if stmt.name in self.declared_vars:
            if stmt.init:
                # For reassignment, use the existing variable's type as target
                var_type = self.var_types.get(stmt.name)
                init_type = self._get_resolved_type(stmt.init)
                init_expr = self._gen_expr(stmt.init, var_type)
                # Convert BigInt -> Int32 if needed
                init_expr = self._convert_to_int32_if_needed(init_expr, init_type, var_type)
                out.write(f"{indent}{stmt.name} = {init_expr};\n")
            return

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

        # First declaration - track the type
        self.declared_vars.add(stmt.name)
        self.var_types[stmt.name] = target_type

        if stmt.type:
            cpp_type = stmt.type.to_cpp()
        elif stmt.init:
            # Check if analyzer resolved the type based on usage
            resolved_type = self.analyzer.var_types.get(id(stmt))
            if resolved_type:
                cpp_type = resolved_type.to_cpp()
            else:
                # Check inferred type - some types need explicit annotation
                inferred_type = self.analyzer.get_expr_type(stmt.init)
                if isinstance(inferred_type, IntLiteralType):
                    cpp_type = BIGINT.to_cpp()
                elif isinstance(inferred_type, BigIntType):
                    # BigInt needs explicit type (auto would infer int from literal)
                    cpp_type = inferred_type.to_cpp()
                elif isinstance(stmt.init, TpyArrayLiteral):
                    # Array literals need explicit type (auto with {...} creates initializer_list)
                    # Resolve IntLiteralType elements to BigInt (Python semantics: [1,2,3] is list of int)
                    if isinstance(inferred_type, ArrayType) and isinstance(inferred_type.element_type, IntLiteralType):
                        resolved_type = ArrayType(BIGINT, inferred_type.size)
                        cpp_type = resolved_type.to_cpp()
                    else:
                        cpp_type = inferred_type.to_cpp() if inferred_type else "auto"
                else:
                    cpp_type = "auto"
        else:
            cpp_type = "auto"

        if stmt.init:
            init_type = self._get_resolved_type(stmt.init)
            init_expr = self._gen_expr(stmt.init, target_type)
            # Convert BigInt -> Int32 if needed
            init_expr = self._convert_to_int32_if_needed(init_expr, init_type, target_type)
            out.write(f"{indent}{cpp_type} {stmt.name} = {init_expr};\n")
        else:
            out.write(f"{indent}{cpp_type} {stmt.name};\n")

    def _gen_assign(self, out: TextIO, stmt: TpyAssign, indent: str) -> None:
        """Generate an assignment."""
        # Special handling for subscript assignment
        if isinstance(stmt.target, TpySubscript):
            obj = self._gen_expr(stmt.target.obj)
            value = self._gen_expr(stmt.value)
            obj_type = self.analyzer.get_expr_type(stmt.target.obj)
            index_type = self.analyzer.get_expr_type(stmt.target.index)
            index_expr = self._gen_index_expr(obj, stmt.target.index, index_type)

            if isinstance(obj_type, StaticListType):
                out.write(f"{indent}{obj}.set({index_expr}, {value});\n")
            else:
                out.write(f"{indent}{obj}[{index_expr}] = {value};\n")
            return

        # Default: simple assignment
        target = self._gen_expr(stmt.target)
        target_type = self.analyzer.get_expr_type(stmt.target)
        value_type = self.analyzer.get_expr_type(stmt.value)
        value = self._gen_expr(stmt.value)
        # Convert BigInt -> Int32 if needed
        value = self._convert_to_int32_if_needed(value, value_type, target_type)
        out.write(f"{indent}{target} = {value};\n")

    def _gen_aug_assign(self, out: TextIO, stmt: TpyAugAssign, indent: str) -> None:
        """Generate an augmented assignment."""
        target = self._gen_expr(stmt.target)
        value = self._gen_expr(stmt.value)
        target_type = self.analyzer.get_expr_type(stmt.target)
        value_type = self._get_resolved_type(stmt.value)

        # Int32 arithmetic needs checked operations
        if isinstance(target_type, Int32Type) and stmt.op in ("+", "-", "*", "//", "%"):
            # Convert BigInt to Int32 if needed
            if self._is_runtime_bigint(stmt.value, value_type):
                value = f"({value}).to_int32()"
            op_map = {"+": "int32_add", "-": "int32_sub", "*": "int32_mul",
                      "//": "int32_div", "%": "int32_mod"}
            out.write(f"{indent}{target} = tpy::{op_map[stmt.op]}({target}, {value});\n")
        else:
            cpp_op = "/" if stmt.op == "//" else stmt.op
            out.write(f"{indent}{target} {cpp_op}= {value};\n")

    def _gen_if(self, out: TextIO, stmt: TpyIf, indent: str) -> None:
        """Generate an if statement."""
        cond = self._gen_expr(stmt.condition)
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
        out.write(f"{indent}while ({cond}) {{\n")

        self.indent_level += 1
        for s in stmt.body:
            self._gen_stmt(out, s)
        self.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_for(self, out: TextIO, stmt: TpyFor, indent: str) -> None:
        """Generate a for loop (range-based)."""
        start = self._gen_expr(stmt.start)
        end = self._gen_expr(stmt.end)
        # Convert BigInt bounds to Int32 (range loops use Int32 counter)
        start_type = self.analyzer.get_expr_type(stmt.start)
        end_type = self.analyzer.get_expr_type(stmt.end)
        if isinstance(start_type, BigIntType):
            start = f"{start}.to_int32()"
        if isinstance(end_type, BigIntType):
            end = f"{end}.to_int32()"
        out.write(f"{indent}for (int32_t {stmt.var} = {start}; {stmt.var} < {end}; ++{stmt.var}) {{\n")

        self.indent_level += 1
        for s in stmt.body:
            self._gen_stmt(out, s)
        self.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection."""
        iterable = self._gen_expr(stmt.iterable)
        iterable_type = self._get_resolved_type(stmt.iterable)

        # Determine element type for the loop variable
        if isinstance(iterable_type, (ListType, ArrayType, SpanType, StaticListType)):
            elem_type = iterable_type.element_type
        elif isinstance(iterable_type, PendingListType):
            elem_type = iterable_type.element_type
        elif isinstance(iterable_type, StrType):
            elem_type = CHAR
        else:
            elem_type = None

        # Resolve IntLiteralType to Int32
        if isinstance(elem_type, IntLiteralType):
            elem_type = INT32

        # For strings, wrap in std::string_view for range-based for
        if isinstance(iterable_type, StrType):
            iterable = f"std::string_view({iterable})"

        # Generate C++ range-based for loop
        if elem_type:
            cpp_type = elem_type.to_cpp()
            out.write(f"{indent}for ({cpp_type} {stmt.var} : {iterable}) {{\n")
        else:
            # Fallback: use auto
            out.write(f"{indent}for (auto {stmt.var} : {iterable}) {{\n")

        self.indent_level += 1
        for s in stmt.body:
            self._gen_stmt(out, s)
        self.indent_level -= 1

        out.write(f"{indent}}}\n")

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
            # Negative literal: items[-1] -> items[items.size() - 1]
            return f"({obj}.size() - {abs_val})"

        index_expr = self._gen_expr(index)
        if self._is_runtime_bigint(index, index_type):
            index_expr = f"{index_expr}.to_int32()"
        return index_expr

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

        elif isinstance(expr, TpyBoolLiteral):
            return "true" if expr.value else "false"

        elif isinstance(expr, TpyStrLiteral):
            if len(expr.value) == 1:
                # Single char - output as C++ char literal
                escaped = expr.value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                return f"'{escaped}'"
            # Multi-char string
            escaped = expr.value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            return f'"{escaped}"'

        elif isinstance(expr, TpyName):
            return expr.name

        elif isinstance(expr, TpyBinOp):
            left_type = self.analyzer.get_expr_type(expr.left)
            right_type = self.analyzer.get_expr_type(expr.right)

            # Handle 'in' and 'not in' operators
            if expr.op in ("in", "not in"):
                left = self._gen_expr(expr.left)
                right = self._gen_expr(expr.right)
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

            # Shift operators need special handling
            if expr.op in ("<<", ">>"):
                # Int32 shifts use checked operations
                if isinstance(left_type, Int32Type):
                    left = self._gen_expr(expr.left)
                    right = self._gen_expr(expr.right)
                    if isinstance(right_type, BigIntType):
                        right = f"({right}).to_int32()"
                    op_func = "int32_lshift" if expr.op == "<<" else "int32_rshift"
                    return f"tpy::{op_func}({left}, {right})"
                # IntLiteralType uses BigInt (Python arbitrary precision semantics)
                if isinstance(left_type, IntLiteralType):
                    left = self._gen_expr(expr.left, BIGINT)
                else:
                    left = self._gen_expr(expr.left)
                # Right operand (shift amount) should be int32
                right = self._gen_expr(expr.right)
                if isinstance(right_type, BigIntType):
                    right = f"({right}).to_int32()"
                return f"({left} {expr.op} {right})"

            # Power operator
            if expr.op == "**":
                # Int32 power uses checked int32_pow
                if self._is_int32_arithmetic(left_type, right_type, expr.op):
                    left = self._gen_expr(expr.left)
                    right = self._gen_expr(expr.right)
                    return f"tpy::int32_pow({left}, {right})"
                # BigInt power
                left = self._gen_expr(expr.left, BIGINT)
                right = self._gen_expr(expr.right, BIGINT)
                return f"({left}).pow({right})"

            # IntLiteralType coerces to the other operand's concrete type
            # BigInt variable + IntLiteral -> promote literal to BigInt
            left_target = BIGINT if isinstance(right_type, BigIntType) else None
            right_target = BIGINT if isinstance(left_type, BigIntType) else None
            left = self._gen_expr(expr.left, left_target)
            right = self._gen_expr(expr.right, right_target)

            # Check if result is Int32 (needs checked arithmetic)
            if self._is_int32_arithmetic(left_type, right_type, expr.op):
                op_map = {"+": "int32_add", "-": "int32_sub", "*": "int32_mul",
                          "//": "int32_div", "%": "int32_mod"}
                if expr.op in op_map:
                    return f"tpy::{op_map[expr.op]}({left}, {right})"

            cpp_op = "/" if expr.op == "//" else expr.op
            return f"({left} {cpp_op} {right})"

        elif isinstance(expr, TpyUnaryOp):
            # For negation, if target is BigInt, promote the operand
            operand = self._gen_expr(expr.operand, target_type)
            operand_type = self.analyzer.get_expr_type(expr.operand)

            # Int32 negation needs checked arithmetic (only for explicit Int32Type)
            if expr.op == "-" and isinstance(operand_type, Int32Type):
                return f"tpy::int32_neg({operand})"

            return f"({expr.op}{operand})"

        elif isinstance(expr, TpyCall):
            # Int32() converts argument to Int32 (with range check for BigInt)
            if expr.func == "Int32":
                if len(expr.args) == 0:
                    return "0"
                arg = expr.args[0]
                arg_type = self.analyzer.get_expr_type(arg)
                gen_arg = self._gen_expr(arg)
                if isinstance(arg_type, BigIntType):
                    return f"{gen_arg}.to_int32()"
                return gen_arg
            # int() constructor for BigInt
            if expr.func == "int":
                if len(expr.args) == 0:
                    return "tpy::BigInt(0)"
                return f"tpy::BigInt({self._gen_expr(expr.args[0])})"
            # print() maps to std::printf
            if expr.func == "print":
                return self._gen_print(expr.args, expr.kwargs)
            # len() maps to .size()
            if expr.func == "len":
                arg = expr.args[0]
                arg_type = self._get_resolved_type(arg)
                # For string literals, wrap in string_view to get .size()
                if isinstance(arg_type, StrType) and isinstance(arg, TpyStrLiteral):
                    escaped = arg.value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                    return f'static_cast<int32_t>(std::string_view("{escaped}").size())'
                # std::array, std::span, std::vector, and std::string_view return std::size_t, cast to int32_t
                if isinstance(arg_type, (ArrayType, SpanType, ListType, StrType)):
                    return f"static_cast<int32_t>({self._gen_expr(arg)}.size())"
                return f"{self._gen_expr(arg)}.size()"
            # chr() maps to static_cast<char>
            if expr.func == "chr":
                arg = expr.args[0]
                arg_type = self.analyzer.get_expr_type(arg)
                gen_arg = self._gen_expr(arg)
                # BigInt (or runtime BigInt like list elements) needs .to_int32() conversion
                if self._is_runtime_bigint(arg, arg_type):
                    return f"static_cast<char>(({gen_arg}).to_int32())"
                return f"static_cast<char>({gen_arg})"
            # Check if this is a function call that needs argument conversion
            func_info = self.analyzer.registry.get_function(expr.func)
            if func_info:
                gen_args = []
                for arg, (pname, ptype) in zip(expr.args, func_info.params):
                    arg_type = self._get_resolved_type(arg)
                    # Pass param type for BigInt promotion
                    gen_arg = self._gen_expr(arg, ptype)
                    # Convert BigInt -> Int32 for Int32 parameters
                    if isinstance(ptype, Int32Type) and self._is_runtime_bigint(arg, arg_type):
                        gen_arg = f"({gen_arg}).to_int32()"
                    # Convert StaticList -> Span
                    if isinstance(ptype, SpanType) and isinstance(arg_type, StaticListType):
                        gen_args.append(f"std::span({gen_arg}.data(), {gen_arg}.size())")
                    # Array literal passed to Span: wrap in std::array (implicit conversion to span<const T>)
                    elif isinstance(ptype, SpanType) and isinstance(arg, TpyArrayLiteral):
                        # Use the expected element type from the span parameter, not the inferred type
                        expected_array_type = ArrayType(ptype.element_type, len(arg.elements))
                        gen_args.append(f"{expected_array_type.to_cpp()}{gen_arg}")
                    # ListType (vector) passed to Span: implicit conversion in C++
                    elif isinstance(ptype, SpanType) and isinstance(arg_type, ListType):
                        # std::vector<T> implicitly converts to std::span<const T>
                        gen_args.append(gen_arg)
                    else:
                        gen_args.append(gen_arg)
                return f"{expr.func}({', '.join(gen_args)})"
            args = ", ".join(self._gen_expr(a) for a in expr.args)
            # Generic type instantiation (e.g., StaticList[T, N]())
            if expr.call_type is not None:
                return f"{expr.call_type.to_cpp()}({args})"
            return f"{expr.func}({args})"

        elif isinstance(expr, TpyMethodCall):
            args = ", ".join(self._gen_expr(a) for a in expr.args)
            # Handle self.method() -> just method() (inside method, implicit this)
            if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
                return f"{expr.method}({args})"
            obj = self._gen_expr(expr.obj)
            obj_type = self._get_resolved_type(expr.obj)
            # Array and Span: .get(i) -> [i], .size() -> .size()
            if isinstance(obj_type, (ArrayType, SpanType)):
                if expr.method == "get":
                    return f"{obj}[{args}]"
                elif expr.method == "size":
                    return f"static_cast<int32_t>({obj}.size())"
            # ListType (std::vector): map Python methods to C++ equivalents
            if isinstance(obj_type, ListType):
                if expr.method == "append":
                    return f"{obj}.push_back({args})"
                elif expr.method == "pop":
                    # pop() returns and removes last element
                    return f"tpy::pop_back({obj})"
                elif expr.method == "clear":
                    return f"{obj}.clear()"
                elif expr.method == "size":
                    return f"static_cast<int32_t>({obj}.size())"
                elif expr.method == "insert":
                    # insert(index, value) -> insert(begin() + index, value)
                    idx = self._gen_expr(expr.args[0])
                    idx_type = self.analyzer.get_expr_type(expr.args[0])
                    if isinstance(idx_type, BigIntType):
                        idx = f"{idx}.to_int32()"
                    val = self._gen_expr(expr.args[1])
                    return f"{obj}.insert({obj}.begin() + {idx}, {val})"
                elif expr.method == "remove":
                    # remove(value) -> erase first occurrence
                    return f"{obj}.erase(std::find({obj}.begin(), {obj}.end(), {args}))"
                elif expr.method == "extend":
                    # extend(other) -> insert at end
                    arg = expr.args[0]
                    if isinstance(arg, TpyArrayLiteral):
                        # Array literal: use initializer_list overload directly
                        other = self._gen_expr(arg)
                        return f"{obj}.insert({obj}.end(), {other})"
                    else:
                        # Variable: use iterator overload
                        other = self._gen_expr(arg)
                        return f"{obj}.insert({obj}.end(), {other}.begin(), {other}.end())"
            return f"{obj}.{expr.method}({args})"

        elif isinstance(expr, TpyFieldAccess):
            # Handle self.field -> just field (inside method, implicit this)
            if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
                return expr.field
            obj = self._gen_expr(expr.obj)
            # Check if obj is a pointer type - use -> instead of .
            obj_type = self.analyzer.get_expr_type(expr.obj)
            if obj_type and obj_type.is_pointer():
                return f"{obj}->{expr.field}"
            return f"{obj}.{expr.field}"

        elif isinstance(expr, TpyArrayLiteral):
            # If target_type is an array or span, use its element type for generating elements
            elem_target = None
            if isinstance(target_type, (ArrayType, SpanType)):
                elem_target = target_type.element_type
            elements = ", ".join(self._gen_expr(e, elem_target) for e in expr.elements)
            return f"{{{elements}}}"

        elif isinstance(expr, TpyListRepeat):
            # [x] * N -> std::vector<T>(N, x)
            # Use target type if provided (for assignment to typed variable)
            if isinstance(target_type, ListType):
                result_type = target_type
            else:
                result_type = self.analyzer.get_expr_type(expr)
            # Resolve IntLiteralType element to BigInt (Python semantics)
            if isinstance(result_type, ListType) and isinstance(result_type.element_type, IntLiteralType):
                result_type = ListType(BIGINT)
            element = self._gen_expr(expr.element)
            count = self._gen_expr(expr.count)
            count_type = self.analyzer.get_expr_type(expr.count)
            # BigInt count needs conversion (IntLiteralType is already plain int)
            if isinstance(count_type, BigIntType):
                count = f"{count}.to_int32()"
            return f"{result_type.to_cpp()}({count}, {element})"

        elif isinstance(expr, TpySubscript):
            obj = self._gen_expr(expr.obj)
            obj_type = self.analyzer.get_expr_type(expr.obj)
            index_type = self.analyzer.get_expr_type(expr.index)
            index_expr = self._gen_index_expr(obj, expr.index, index_type)
            # StaticList uses .get() method, Array and Span use []
            if isinstance(obj_type, StaticListType):
                return f"{obj}.get({index_expr})"
            return f"{obj}[{index_expr}]"

        return "/* unknown expr */"

    def _involves_variables(self, expr: TpyExpr) -> bool:
        """Check if an expression involves any variable references."""
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

    def _convert_to_int32_if_needed(self, gen_expr: str, actual_type: TpyType, expected_type: TpyType) -> str:
        """Wrap expression with .to_int32() if converting BigInt to Int32."""
        if isinstance(expected_type, Int32Type) and isinstance(actual_type, BigIntType):
            return f"{gen_expr}.to_int32()"
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
                parts.append(f'{self._gen_expr(arg)}.to_string()')
            elif isinstance(arg_type, (ListType, ArrayType, SpanType, StaticListType)):
                if isinstance(arg, TpyArrayLiteral):
                    # Array literals need explicit type for ListPrinter CTAD
                    cpp_type = arg_type.to_cpp()
                    parts.append(f'tpy::ListPrinter({cpp_type}{self._gen_expr(arg)})')
                else:
                    parts.append(f'tpy::ListPrinter({self._gen_expr(arg)})')
            else:
                # Int32, Char, Bool, literals, etc. - direct output
                parts.append(self._gen_expr(arg))

        # Add end string
        if end_str:
            parts.append(f'"{end_str}"')

        return "std::cout << " + " << ".join(parts)
