"""
TurboPython Expression Code Generation

Generates C++ code from TurboPython expressions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, BoolType, StrType, CharType,
    NamedType, PtrType, ConstPtrType, OwnType, OptionalType, NoneType, ArrayType, ListType, PendingListType,
    SpanType, TypeParamRef,
    INT32, BIGINT, FLOAT, CHAR, VOID, is_protocol_type,
    ResolvedBinop
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce
)
from ..namespace import BindingKind
from .context import escape_cpp_string, escape_cpp_char, module_to_cpp_namespace, expand_cpp_template

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .builtins import BuiltinGenerator
    from .protocols import ProtocolGenerator

from tpyc import modules as builtin_modules


class ExpressionGenerator:
    """Generates C++ code from TurboPython expressions."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        builtins: BuiltinGenerator,
        protocols: ProtocolGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.builtins = builtins
        self.protocols = protocols
        # Wire up builtins to use our gen_expr methods
        self.builtins.set_expr_generator(self.gen_expr, self.gen_expr_deref)

    def gen_expr_deref(self, expr: TpyExpr, target_type: TpyType = None) -> str:
        """Generate an expression, dereferencing globals.

        Use this when the underlying value is needed (e.g., method calls,
        operators, function arguments). For assignment targets, use gen_expr.
        """
        result = self.gen_expr(expr, target_type)
        if self.ctx.is_indirect_name(expr):
            result = f"(*{result})"
        # Value optionals are represented as std::optional<T> and must be
        # unwrapped when a concrete value is required.
        expr_type = self.types.get_resolved_type(expr)
        analyzed_type = self.ctx.analyzer.get_expr_type(expr)
        proven_optional_expr = expr.narrowed_optional_proven
        if (
            target_type is not None
            and (
                (isinstance(expr_type, OptionalType) and expr_type.inner.is_value_type())
                or proven_optional_expr
            )
            and not isinstance(target_type, OptionalType)
        ):
            # If sema already narrowed this expression to non-Optional, unwrap
            # without an extra runtime check. Otherwise keep checked dereference.
            if proven_optional_expr:
                result = f"(*{result})"
            elif isinstance(analyzed_type, OptionalType):
                result = f"tpy::deref_optional({result})"
            else:
                result = f"(*{result})"
        return result

    def gen_expr(self, expr: TpyExpr, target_type: TpyType = None) -> str:
        """Generate an expression.

        Args:
            expr: The expression to generate
            target_type: Optional expected type (for implicit promotion)
        """
        if isinstance(expr, TpyIntLiteral):
            # Promote to BigInt if target expects it
            if isinstance(target_type, BigIntType):
                if -2**31 <= expr.value <= 2**31 - 1:
                    return f"tpy::BigInt({expr.value})"
                elif -2**63 <= expr.value <= 2**63 - 1:
                    # Use static_cast for portability (int64_t is long on Linux, long long on macOS)
                    return f"tpy::BigInt(static_cast<int64_t>({expr.value}LL))"
                else:
                    # TODO: support arbitrary precision literals via string parsing
                    raise ValueError(f"Integer literal {expr.value} exceeds int64 range")
            return str(expr.value)

        elif isinstance(expr, TpyFloatLiteral):
            # C++ accepts Python-style float literals directly
            return repr(expr.value)

        elif isinstance(expr, TpyBoolLiteral):
            return "true" if expr.value else "false"

        elif isinstance(expr, TpyNoneLiteral):
            if isinstance(target_type, OwnType) and isinstance(target_type.wrapped, OptionalType):
                return "std::nullopt"
            if isinstance(target_type, OptionalType):
                return "std::nullopt"
            return "nullptr"

        elif isinstance(expr, TpyCoerce):
            if expr.coercion.name == "int_literal_to_int32" or isinstance(expr.expected_type, SpanType):
                inner_target = expr.expected_type
            else:
                inner_target = expr.actual_type
            gen_inner = self.gen_expr(expr.expr, inner_target)
            if isinstance(expr.expected_type, SpanType):
                return self._gen_span_coercion(expr.expr, expr.expected_type, gen_inner)
            # IntLiteralType may be runtime BigInt; sema records this on the coercion.
            if expr.coercion.name == "int_literal_to_int32":
                if expr.runtime_bigint:
                    return f"({gen_inner}).to_int32()"
                return gen_inner
            # Coercions that call methods on the inner expression need dereferencing for globals
            if expr.coercion.name in ("record_to_ptr", "record_to_const_ptr", "bigint_to_int32"):
                if self.ctx.is_indirect_name(expr.expr):
                    gen_inner = f"(*{gen_inner})"
            return expr.coercion.codegen(gen_inner, expr.actual_type, expr.expected_type, expr.context_kind)

        elif isinstance(expr, TpyStrLiteral):
            # If target type is Char and single char, output as char literal
            if isinstance(target_type, CharType) and len(expr.value) == 1:
                return f"'{escape_cpp_char(expr.value)}'"
            return f'"{escape_cpp_string(expr.value)}"'

        elif isinstance(expr, TpyName):
            # self → (*this) only in instance methods (self is implicit receiver, not a param)
            if expr.name == "self" and self.ctx.in_method and "self" not in self.ctx.current_func_params:
                return "(*this)"
            # Check if this is an imported variable from a user module
            if expr.name in self.ctx.user_imported_variables:
                # Don't qualify if shadowed by a local variable
                if expr.name in self.ctx.local_scope_names:
                    return expr.name
                # Check if redefined at top level
                if expr.name in self.ctx.top_level_decls:
                    decl_line = self.ctx.top_level_decls[expr.name]
                    # In a function (current_stmt_line == 0): always use local
                    # At top level: use local only if current line >= declaration line
                    if self.ctx.current_stmt_line == 0 or self.ctx.current_stmt_line >= decl_line:
                        return expr.name
                # Use qualified import reference (convert dotted name to C++ namespace)
                source_module, original_name = self.ctx.user_imported_variables[expr.name]
                return f"{module_to_cpp_namespace(source_module)}::{original_name}"
            return expr.name

        elif isinstance(expr, TpyBinOp):
            return self._gen_binop(expr, target_type)

        elif isinstance(expr, TpyUnaryOp):
            return self._gen_unaryop(expr, target_type)

        elif isinstance(expr, TpyCall):
            return self._gen_call(expr)

        elif isinstance(expr, TpyMethodCall):
            return self._gen_method_call(expr)

        elif isinstance(expr, TpyFieldAccess):
            return self._gen_field_access(expr)

        elif isinstance(expr, TpyArrayLiteral):
            return self._gen_array_literal(expr, target_type)

        elif isinstance(expr, TpyListRepeat):
            return self._gen_list_repeat(expr, target_type)

        elif isinstance(expr, TpySubscript):
            return self._gen_subscript(expr)

        return "/* unknown expr */"

    def gen_truthy_expr(self, expr: TpyExpr) -> str:
        """Generate a bool expression using Python-style truthiness semantics.

        For Optional value types, truthiness means "has value and contained value is truthy".
        """
        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            operand_truthy = self.gen_truthy_expr(expr.operand)
            return f"(!({operand_truthy}))"
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            left = self.gen_truthy_expr(expr.left)
            right = self.gen_truthy_expr(expr.right)
            return f"({left} {expr.op} {right})"

        expr_type = self.types.get_resolved_type(expr)
        rendered = self.gen_expr(expr)
        if isinstance(expr_type, OptionalType) and expr_type.inner.is_value_type():
            return f"tpy::is_truthy({rendered})"
        return rendered

    def _gen_binop(self, expr: TpyBinOp, target_type: TpyType | None) -> str:
        """Generate binary operation code."""
        # First pass: get raw types to detect Int32 operands
        left_raw = self.types.get_resolved_type(expr.left)
        right_raw = self.types.get_resolved_type(expr.right)
        # If one operand is Int32, resolve literals as Int32 (not BigInt)
        int32_context = target_type if isinstance(target_type, Int32Type) else None
        if isinstance(left_raw, Int32Type) or isinstance(right_raw, Int32Type):
            int32_context = INT32
        left_type = self.types.get_resolved_type(expr.left, int32_context)
        right_type = self.types.get_resolved_type(expr.right, int32_context)

        # Handle 'in' and 'not in' operators
        if expr.op in ("in", "not in"):
            left = self.gen_expr(expr.left)
            right = self.gen_expr(expr.right)
            # Dereference globals for .begin()/.end() calls
            if self.ctx.is_indirect_name(expr.right):
                right = f"(*{right})"
            right_resolved = self.types.get_resolved_type(expr.right)
            if isinstance(right_resolved, StrType):
                # String contains: use std::string_view::find
                find_expr = f"(std::string_view({right}).find({left}) != std::string_view::npos)"
            else:
                # Collection: use std::find
                find_expr = f"(std::find({right}.begin(), {right}.end(), {left}) != {right}.end())"
            if expr.op == "not in":
                return f"(!{find_expr})"
            return find_expr

        # Identity operators (is / is not) — nullable comparison
        if expr.op in ("is", "is not"):
            # Use resolved (declared) types here, not flow-narrowed analyzer types.
            # A narrowed Optional[T] name may currently analyze as T, but identity
            # checks against None still need Optional semantics in codegen.
            left_type = self.types.get_resolved_type(expr.left)
            right_type = self.types.get_resolved_type(expr.right)
            # Determine which side is the Optional expression
            opt_expr = None
            if isinstance(left_type, OptionalType) and isinstance(expr.right, TpyNoneLiteral):
                opt_expr = expr.left
            elif isinstance(right_type, OptionalType) and isinstance(expr.left, TpyNoneLiteral):
                opt_expr = expr.right
            if opt_expr is not None:
                # Indirect names (T* pointer-locals/globals) use pointer comparison
                if self.ctx.is_indirect_name(opt_expr):
                    val = self.gen_expr(opt_expr)
                    cpp_op = "==" if expr.op == "is" else "!="
                    return f"({val} {cpp_op} nullptr)"
                # Everything else (value-type optionals, field accesses) uses .has_value()
                val = self.gen_expr(opt_expr)
                if expr.op == "is":
                    return f"(!{val}.has_value())"
                else:
                    return f"({val}.has_value())"
            # Fallback: pointer comparison
            cpp_op = "==" if expr.op == "is" else "!="
            left = self.gen_expr(expr.left)
            right = self.gen_expr(expr.right)
            return f"({left} {cpp_op} {right})"

        # Logical operators - generate C++ directly.
        if expr.op in ("&&", "||"):
            left = self.gen_expr_deref(expr.left)
            right = self.gen_expr_deref(expr.right)
            return f"({left} {expr.op} {right})"

        # Comparison operators - generate C++ directly.
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            # Optional value operands in comparisons must be unwrapped:
            # - narrowed/proven: unchecked (*x)
            # - unproven: checked tpy::deref_optional(x)
            left_target = None
            right_target = None
            if isinstance(left_type, OptionalType) and left_type.inner.is_value_type():
                left_target = left_type.inner
            if isinstance(right_type, OptionalType) and right_type.inner.is_value_type():
                right_target = right_type.inner
            # When comparing Char with string literal, output literal as char.
            # Keep Optional unwrapping target when present.
            if left_target is None and isinstance(right_type, CharType):
                left_target = CHAR
            if right_target is None and isinstance(left_type, CharType):
                right_target = CHAR
            # Use gen_expr_deref for pointer-locals/globals (T* needs dereferencing)
            left = self.gen_expr_deref(expr.left, left_target)
            right = self.gen_expr_deref(expr.right, right_target)
            return f"({left} {expr.op} {right})"

        # Optimization: IntLiteral op IntLiteral with Int32 target → direct Int32 arithmetic
        # This avoids unnecessary BigInt heap allocations
        # Use analyzer types for this check - analyzer returns IntLiteralType for all-literal
        # expressions (including nested binops like 2+3), while get_resolved_type returns BigInt
        # NOTE: Must also check operands aren't variables (loop vars have IntLiteralType but aren't literals)
        left_analyzer_type = self.ctx.analyzer.get_expr_type(expr.left)
        right_analyzer_type = self.ctx.analyzer.get_expr_type(expr.right)
        left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
        right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)
        if (isinstance(target_type, Int32Type) and left_is_literal and right_is_literal):
            # Pass target_type to handle nested binops like 1 + (2 + 3)
            left = self.gen_expr(expr.left, target_type)
            right = self.gen_expr(expr.right, target_type)
            # Use registry to get Int32 binary operator
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                cpp_template = self.builtins.get_type_method_template(INT32, method_name)
                if cpp_template:
                    return expand_cpp_template(cpp_template, left, right)
            # Fallback for operators not in module system (bitwise operators)
            return f"({left} {expr.op} {right})"

        # Use resolved binop from sema (builtin arithmetic/bitwise operators)
        if binop_result := expr.resolved_binop:
            # Get types for proper literal promotion
            # FunctionInfo.params is list[tuple[str, TpyType]]
            param_type = binop_result.method.params[0].type if binop_result.method.params else None
            receiver_type = binop_result.receiver_type
            # For reverse operators, {self} is the right operand, {0} is left
            # For forward operators, {self} is the left operand, {0} is right
            if binop_result.is_reverse:
                # right is {self} (receiver), left is {0} (argument)
                left = self.gen_expr_deref(expr.left, param_type)
                right = self.gen_expr_deref(expr.right, receiver_type)
                # Convert argument if needed (e.g., IntLiteralType that's actually BigInt)
                left_actual = self.types.get_resolved_type(expr.left, param_type)
                left = self._convert_to_int32_arg(left, left_actual, param_type, expr.left)
            else:
                # left is {self} (receiver), right is {0} (argument)
                left = self.gen_expr_deref(expr.left, receiver_type)
                right = self.gen_expr_deref(expr.right, param_type)
                # Convert argument if needed (e.g., IntLiteralType that's actually BigInt)
                right_actual = self.types.get_resolved_type(expr.right, param_type)
                right = self._convert_to_int32_arg(right, right_actual, param_type, expr.right)
            # Generate binop using helper (handles wrappers and is_reverse)
            result = self._gen_binop_from_result(binop_result, left, right)
            # Wrap in parens to avoid precedence issues with cout << and other operators
            return f"({result})"

        # Fallback for IntLiteral + IntLiteral → BigInt (arbitrary precision)
        # (Int32 case is handled earlier as an optimization)
        if isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType):
            left = self.gen_expr(expr.left, BIGINT)
            right = self.gen_expr(expr.right, BIGINT)
            cpp_op = "/" if expr.op == "//" else expr.op
            return f"({left} {cpp_op} {right})"

        # Protocol-typed operands - use C++ operator syntax
        # The protocol constraint guarantees the operator exists
        if is_protocol_type(left_type):
            left = self.gen_expr_deref(expr.left, left_type)
            right = self.gen_expr_deref(expr.right, right_type)
            # Map Python operators to C++ operators
            cpp_op = expr.op
            if expr.op == "//":
                cpp_op = "/"  # Floor division maps to / in C++
            return f"({left} {cpp_op} {right})"

        # User-defined types (records) - use generated C++ operator
        if isinstance(left_type, NamedType) and left_type.is_record:
            left = self.gen_expr_deref(expr.left, left_type)
            right = self.gen_expr_deref(expr.right, right_type)
            # Map Python operators to C++ operators
            cpp_op = expr.op
            if expr.op == "//":
                cpp_op = "/"  # Floor division maps to / in C++
            return f"({left} {cpp_op} {right})"

        raise RuntimeError(f"No codegen for binary operator {expr.op} with {left_type} and {right_type}")

    def _gen_unaryop(self, expr: TpyUnaryOp, target_type: TpyType | None) -> str:
        """Generate unary operation code."""
        # Logical not
        if expr.op == "!":
            return f"(!({self.gen_truthy_expr(expr.operand)}))"

        operand_type = self.ctx.analyzer.get_expr_type(expr.operand)
        resolved_operand_type = self.types.get_resolved_type(expr.operand)
        unary_target = target_type
        if isinstance(resolved_operand_type, OptionalType) and resolved_operand_type.inner.is_value_type():
            unary_target = resolved_operand_type.inner
        operand = self.gen_expr_deref(expr.operand, unary_target)

        # Use resolved unary op from sema
        if unaryop_result := expr.resolved_unaryop:
            return expand_cpp_template(unaryop_result.method.cpp_template, operand)

        # Fallback for IntLiteralType (not in module system)
        if isinstance(operand_type, IntLiteralType):
            return f"({expr.op}{operand})"

        raise RuntimeError(f"No codegen for unary operator {expr.op} with {operand_type}")

    def _gen_call(self, expr: TpyCall) -> str:
        """Generate function call code."""
        # Check if it's a builtin type constructor (e.g., int from builtins, Int32 from tpy)
        for module_name in ["builtins", "tpy"]:
            qname = f"{module_name}.{expr.func}"
            if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                if record_info.constructors and not record_info.type_params:
                    return self.builtins.gen_builtin_constructor(expr, record_info)
        # print() maps to std::printf
        if expr.func == "print":
            return self.builtins.gen_print(expr.args, expr.kwargs)
        # Check registry for built-in functions
        if overloads := self.ctx.analyzer.registry.get_builtin_function_overloads(expr.func):
            if not overloads[0].special_handling:
                return self.builtins.gen_builtin_function_overloads(expr, overloads)
        # Check for imported function (from X import Y -> Y())
        # Only if not shadowed by a variable, user-defined function, or record
        if expr.func in self.ctx.analyzer.imported_names:
            is_shadowed = (expr.func in self.ctx.declared_vars or
                           expr.func in self.ctx.global_names or
                           self.ctx.analyzer.registry.get_function(expr.func) is not None or
                           self.ctx.analyzer.registry.get_record(expr.func) is not None)
            if not is_shadowed:
                module_name, func_name = self.ctx.analyzer.imported_names[expr.func]
                # copy(x) from tpy - dereference pointer-locals to get the value
                if module_name == "tpy" and func_name == "copy":
                    arg = expr.args[0]
                    arg_type = self.ctx.analyzer.get_expr_type(arg)
                    # Optional non-value: keep native representation (T* or std::optional<T>)
                    if isinstance(arg_type, OptionalType) and not arg_type.inner.is_value_type():
                        return self.gen_expr(arg)
                    return self.gen_expr_deref(arg)
                # Check for module function
                module_info = self.ctx.analyzer.registry.get_module(module_name)
                if module_info and func_name in module_info.functions:
                    return self.builtins.gen_builtin_function_overloads(expr, module_info.functions[func_name])
                # Check for type constructor (e.g., Int32 from tpy, int from builtins)
                qname = f"{module_name}.{func_name}"
                if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                    if record_info.constructors and not record_info.type_params:
                        return self.builtins.gen_builtin_constructor(expr, record_info)
        # Check if this is a function call that needs argument conversion
        func_info = self.ctx.analyzer.registry.get_function(expr.func)
        if func_info:
            # Build type substitution for generic functions
            type_subst = {}
            if func_info.is_generic() and expr.inferred_type_args:
                type_subst = dict(zip(func_info.type_params, expr.inferred_type_args))

            gen_args = []
            for arg, (pname, ptype) in zip(expr.args, func_info.params):
                # Resolve TypeParamRef for generic functions
                resolved_ptype = self.types.substitute_type_params(ptype, type_subst) if type_subst else ptype

                # Optional non-value params are T* — pass raw pointer
                if isinstance(resolved_ptype, OptionalType) and not resolved_ptype.inner.is_value_type():
                    if isinstance(arg, TpyNoneLiteral):
                        gen_args.append("nullptr")
                    elif self.ctx.is_indirect_name(arg):
                        # Already a T* pointer-local/global — pass as-is
                        gen_args.append(self.gen_expr(arg, resolved_ptype))
                    elif isinstance(self.ctx.analyzer.get_expr_type(arg), OptionalType):
                        arg_gen = self.gen_expr(arg, resolved_ptype)
                        if isinstance(arg, TpyFieldAccess):
                            # Field access produces std::optional<T>, convert to T*
                            gen_args.append(f"tpy::optional_to_ptr({arg_gen})")
                        else:
                            # Expression already produces T* (e.g. function returning Optional)
                            gen_args.append(arg_gen)
                    else:
                        # Lvalue reference — take address
                        gen_args.append(f"&({self.gen_expr(arg, resolved_ptype)})")
                # Temporaries passed to mutable reference params need a temp variable
                # because C++ can't bind rvalue to non-const lvalue reference
                # TypeParamRef generates param_val_or_ref_t<T> which is T& for object types
                elif (resolved_ptype.is_ref_param() or isinstance(ptype, TypeParamRef)) and self.ctx.is_temporary_expr(arg):
                    init_expr = self.gen_expr(arg, resolved_ptype)
                    temp_name = self.ctx.temps.create(resolved_ptype, init_expr)
                    gen_args.append(temp_name)
                else:
                    # Pass param type for BigInt promotion
                    # Dereference globals for function arguments
                    gen_arg = self.gen_expr_deref(arg, resolved_ptype)
                    gen_args.append(gen_arg)

            # Determine function name
            func_cpp_name = expr.func
            if func_info.is_native_import or func_info.is_extern_c:
                # @native/@native_c/@extern_c: use the C/C++ symbol name directly.
                # For @native_c, the calling module's header has a local re-declaration
                # so no namespace qualification is needed (works for same-module,
                # cross-module, and package re-export cases).
                func_cpp_name = func_info.native_name or func_info.name
            elif expr.func in self.ctx.user_imported_functions:
                source_module, original_name = self.ctx.user_imported_functions[expr.func]
                func_cpp_name = f"{module_to_cpp_namespace(source_module)}::{original_name}"

            # For generic functions, always emit explicit type args to avoid C++ deduction issues
            # with tpy::param_val_or_ref_t<T> parameters
            if func_info.is_generic() and expr.inferred_type_args:
                type_args_str = ", ".join(t.to_cpp() for t in expr.inferred_type_args)
                return f"{func_cpp_name}<{type_args_str}>({', '.join(gen_args)})"
            return f"{func_cpp_name}({', '.join(gen_args)})"
        # Generic type instantiation (e.g., Container[T, N]())
        if expr.call_type is not None:
            # Pointer null constructors: Ptr[T]() / ConstPtr[T]() → nullptr
            if isinstance(expr.call_type, (PtrType, ConstPtrType)) and not expr.args:
                return "nullptr"
            # List repeat already generates the target type via from_range
            if len(expr.args) == 1 and isinstance(expr.args[0], TpyListRepeat):
                return self.gen_expr(expr.args[0], expr.call_type)
            # Use sema-resolved constructor when available (e.g. list(iterable))
            if (expr.args and not isinstance(expr.args[0], TpyArrayLiteral)
                    and expr.resolved_function_info
                    and expr.resolved_function_info.cpp_template):
                ctor = expr.resolved_function_info
                type_params = builtin_modules.extract_type_params(expr.call_type)
                gen_args = []
                for a in expr.args:
                    gen = self.gen_expr(a, expr.call_type)
                    if self.ctx.is_indirect_name(a):
                        gen = f"(*{gen})"
                    gen_args.append(gen)
                return self.builtins.apply_cpp_template(ctor.cpp_template, gen_args, type_params, expr.call_type)
            # Pass call_type as target for proper nested array brace generation
            args = ", ".join(self.gen_expr_deref(a, expr.call_type) for a in expr.args)
            # Use qualified type name for imported records
            type_cpp = self.types.type_to_cpp(expr.call_type)
            return f"{type_cpp}({args})"
        # Check for user-defined record constructor (e.g., Point(1, 2))
        if record_info := self.ctx.analyzer.registry.get_record(expr.func):
            init_info = record_info.get_method("__init__")
            init_params = init_info.params if init_info else []
            gen_args = []
            for i, a in enumerate(expr.args):
                ptype = init_params[i].type if i < len(init_params) else None
                # Optional non-value params are T* — same logic as function calls
                if isinstance(ptype, OptionalType) and not ptype.inner.is_value_type():
                    if isinstance(a, TpyNoneLiteral):
                        gen_args.append("nullptr")
                    elif self.ctx.is_indirect_name(a):
                        gen_args.append(self.gen_expr(a, ptype))
                    elif isinstance(self.ctx.analyzer.get_expr_type(a), OptionalType):
                        arg_gen = self.gen_expr(a, ptype)
                        if isinstance(a, TpyFieldAccess):
                            gen_args.append(f"tpy::optional_to_ptr({arg_gen})")
                        else:
                            gen_args.append(arg_gen)
                    else:
                        gen_args.append(f"&({self.gen_expr(a, ptype)})")
                else:
                    gen_args.append(self.gen_expr_deref(a))
            args = ", ".join(gen_args)
            # Native records: use native C++ name
            if record_info.is_native:
                cpp_name = record_info.native_name or expr.func
                # @native_c: aggregate init (POD struct)
                if record_info.is_native_c:
                    return f"{cpp_name}{{{args}}}"
                # @native: constructor call (C++ class)
                return f"{cpp_name}({args})"
            # Qualify imported records (use original name for aliases)
            if expr.func in self.ctx.user_imported_records:
                source_module, original_name = self.ctx.user_imported_records[expr.func]
                return f"{module_to_cpp_namespace(source_module)}::{original_name}({args})"
            return f"{expr.func}({args})"
        args = ", ".join(self.gen_expr(a) for a in expr.args)
        return f"{expr.func}({args})"

    def _gen_method_call(self, expr: TpyMethodCall) -> str:
        """Generate method call code."""
        if expr.resolved_function_info:
            params = expr.resolved_function_info.params
            gen_args = []
            for i, arg in enumerate(expr.args):
                ptype = params[i].type if i < len(params) else None
                gen_args.append(self.gen_expr_deref(arg, ptype))
            args = ", ".join(gen_args)
        else:
            args = ", ".join(self.gen_expr_deref(a) for a in expr.args)

        # Handle user module function calls: module.func() -> tpy_user::module::func()
        if expr.user_module_call is not None:
            ns = module_to_cpp_namespace(expr.user_module_call)
            fi = expr.resolved_function_info
            if fi and (fi.is_native_import or fi.is_extern_c):
                func_name = fi.native_name or fi.name
                return f"{ns}::{func_name}({args})"
            return f"{ns}::{expr.method}({args})"

        # Handle builtin module function/type calls (e.g., time.time() or t.Int32() with import tpy as t)
        if expr.builtin_module_call is not None:
            module_name = expr.builtin_module_call
            module_info = self.ctx.analyzer.registry.get_module(module_name)
            if module_info and expr.method in module_info.functions:
                from ..parse import TpyCall
                temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                return self.builtins.gen_builtin_function_overloads(temp_call, module_info.functions[expr.method])
            # Check for type constructor (e.g., tpy.Int32)
            qname = f"{module_name}.{expr.method}"
            if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                if record_info.constructors and not record_info.type_params:
                    from ..parse import TpyCall
                    temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                    return self.builtins.gen_builtin_constructor(temp_call, record_info)

        # Check for inherited builtin method with cpp_template first
        # This must be checked before the self.method() shortcut because
        # inherited builtin methods need the cpp_template substitution
        if expr.resolved_function_info:
            method_info = expr.resolved_function_info
            if method_info.cpp_template:
                # For self.inherited_method(), use (*this) as the receiver
                if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
                    return self.builtins.gen_method_from_function_info("(*this)", expr.args, method_info)
                obj = self.gen_expr(expr.obj)
                if expr.obj.narrowed_optional_proven:
                    obj = f"(*{obj})"
                method_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
                return self.builtins.gen_method_from_function_info(method_obj, expr.args, method_info)

        # Handle self.method() -> just method() (inside method, implicit this)
        if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
            return f"{expr.method}({args})"
        # Handle super().method() -> ParentClass::method(args)
        if expr.super_parent_type is not None:
            parent_cpp = expr.super_parent_type.to_cpp()
            return f"{parent_cpp}::{expr.method}({args})"
        # Handle ClassName.staticmethod() -> ClassName::staticmethod()
        if expr.is_static_call and isinstance(expr.obj, TpyName):
            # For native records, use the C++ class and method names
            record_info = self.ctx.analyzer.registry.get_record(expr.obj.name)
            if record_info and record_info.is_native:
                cpp_class = record_info.native_name or expr.obj.name
                cpp_method = expr.resolved_function_info.native_name if expr.resolved_function_info and expr.resolved_function_info.native_name else expr.method
                return f"{cpp_class}::{cpp_method}({args})"
            return f"{expr.obj.name}::{expr.method}({args})"
        # Handle module.function() (import X -> X.func())
        # Only if the name isn't shadowed by a variable, user-defined function, or record
        if isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.analyzer.imports:
            module_name = expr.obj.name
            # Check if shadowed by variable, user-defined function, or record
            is_shadowed = (module_name in self.ctx.declared_vars or
                           module_name in self.ctx.global_names or
                           self.ctx.analyzer.registry.get_function(module_name) is not None or
                           self.ctx.analyzer.registry.get_record(module_name) is not None)
            if not is_shadowed:
                if self.ctx.analyzer.imports[module_name] is None:
                    module_info = self.ctx.analyzer.registry.get_module(module_name)
                    if module_info and expr.method in module_info.functions:
                        # Create a temp call for code generation
                        from ..parse import TpyCall
                        temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                        return self.builtins.gen_builtin_function_overloads(temp_call, module_info.functions[expr.method])
        obj = self.gen_expr(expr.obj)
        if expr.obj.narrowed_optional_proven:
            obj = f"(*{obj})"
        obj_type = self.types.get_resolved_type(expr.obj)

        # Unwrap OwnType for method lookup - Own[T] behaves as T for method calls
        if isinstance(obj_type, OwnType):
            obj_type = obj_type.wrapped

        # Builtin methods with cpp_template (resolved by sema)
        if expr.resolved_function_info:
            method_info = expr.resolved_function_info
            if method_info.cpp_template:
                # T* pointer-locals/globals need dereferencing for method template access
                method_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
                return self.builtins.gen_method_from_function_info(method_obj, expr.args, method_info)

        # User-defined record methods may need temp handling for TypeParamRef params
        # TypeParamRef generates param_val_or_ref_t<T> which is T& for object types
        # Temporaries can't bind to non-const lvalue reference
        if isinstance(obj_type, NamedType) and obj_type.is_record:
            record_info = self.ctx.analyzer.registry.get_record(obj_type.name)
            if record_info:
                method_info = record_info.get_method(expr.method)
                if method_info:
                    # Build type substitution: {"T": Int32} for Box[Int32]
                    type_subst = {}
                    if record_info.type_params and obj_type.type_args:
                        for param_name, arg_type in zip(record_info.type_params, obj_type.type_args):
                            type_subst[param_name] = arg_type
                    gen_args = []
                    for arg, (pname, ptype) in zip(expr.args, method_info.params):
                        if isinstance(ptype, TypeParamRef) and self.ctx.is_temporary_expr(arg):
                            # Resolve TypeParamRef to actual type
                            resolved_type = type_subst.get(ptype.name, ptype)
                            # Only need temp for object types (T&), not value types (const T&)
                            if not resolved_type.is_value_type():
                                init_expr = self.gen_expr(arg, resolved_type)
                                temp_name = self.ctx.temps.create(resolved_type, init_expr)
                                gen_args.append(temp_name)
                            else:
                                gen_args.append(self.gen_expr_deref(arg))
                        else:
                            gen_args.append(self.gen_expr_deref(arg))
                    args = ", ".join(gen_args)

        # Use -> for pointer-locals/globals (T*) and pointer-typed expressions
        # (OptionalType non-value expressions like function calls return T*)
        obj_type = self.ctx.analyzer.get_expr_type(expr.obj)
        is_optional_ptr = isinstance(obj_type, OptionalType) and not obj_type.inner.is_value_type()
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"tpy::deref_optional({obj}).{expr.method}({args})"
            # For pointer-globals with wrapper storage, this yields raw `T*`.
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"tpy::deref_ptr({ptr_expr}).{expr.method}({args})"
        use_arrow = self.ctx.is_indirect_name(expr.obj) or (obj_type and obj_type.is_pointer()) or is_optional_ptr
        accessor = "->" if use_arrow else "."
        # Use native method name if available (for @native/@native_c class methods)
        cpp_method = expr.resolved_function_info.native_name if expr.resolved_function_info and expr.resolved_function_info.native_name else expr.method
        return f"{obj}{accessor}{cpp_method}({args})"

    def _gen_field_access(self, expr: TpyFieldAccess) -> str:
        """Generate field access code."""
        # Handle self.field -> this->field (inside method)
        # Using this-> avoids shadowing issues when field name matches parameter name
        if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
            return f"this->{expr.field}"

        # Check for module variable access (e.g., sys.argv)
        if isinstance(expr.obj, TpyName):
            if self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.MODULE:
                    # Get actual module name (may differ from local name for aliased imports)
                    module_name = binding.import_source[0] if binding.import_source else expr.obj.name
                    module_info = self.ctx.analyzer.registry.get_module(module_name)
                    if module_info and expr.field in module_info.variables:
                        return module_info.variables[expr.field].cpp_expr

        obj = self.gen_expr(expr.obj)
        if expr.obj.narrowed_optional_proven:
            obj = f"(*{obj})"
        # Check if obj is a pointer type or global - use -> instead of .
        obj_type = self.ctx.analyzer.get_expr_type(expr.obj)
        is_indirect = self.ctx.is_indirect_name(expr.obj)
        is_optional_ptr = isinstance(obj_type, OptionalType) and not obj_type.inner.is_value_type()
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"tpy::deref_optional({obj}).{expr.field}"
            # For pointer-globals with wrapper storage, this yields raw `T*`.
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"tpy::deref_ptr({ptr_expr}).{expr.field}"
        if obj_type and obj_type.is_pointer():
            if is_indirect and not isinstance(obj_type, (PtrType, ConstPtrType)):
                # Global pointer wrapper needs deref first: Global<Ptr<T>> -> (*global)->field
                return f"(*{obj})->{expr.field}"
            return f"{obj}->{expr.field}"
        if is_indirect or is_optional_ptr:
            return f"{obj}->{expr.field}"
        return f"{obj}.{expr.field}"

    def _gen_array_literal(self, expr: TpyArrayLiteral, target_type: TpyType | None) -> str:
        """Generate array literal code."""
        # Some types need explicit element targeting (Array, Span)
        # Others handle implicit conversions (list, etc.)
        elem_target = None
        if target_type and target_type.needs_explicit_element_target():
            elem_target = target_type.get_element_type()
        elements = ", ".join(self.gen_expr_deref(e, elem_target) for e in expr.elements)
        literal = f"{{{elements}}}"
        # std::array of std::array needs an extra brace level
        if isinstance(elem_target, ArrayType):
            return f"{{{literal}}}"
        # Empty list needs explicit type to avoid ambiguity with T* assignment
        if not expr.elements and target_type and target_type.get_element_type() is not None:
            return f"{target_type.to_cpp()}{literal}"
        return literal

    def _gen_list_repeat(self, expr: TpyListRepeat, target_type: TpyType | None) -> str:
        """Generate list repeat code."""
        # [elements...] * N -> repeated sequence
        # Note: Empty list repetition [] * N is collapsed to [] in the parser

        count = self.gen_expr_deref(expr.count)
        count_type = self.ctx.analyzer.get_expr_type(expr.count)
        # BigInt count needs conversion (IntLiteralType is already plain int)
        if isinstance(count_type, BigIntType):
            count = f"{count}.to_int32()"

        # Determine result type and element type
        if target_type is not None:
            result_type = target_type
            elem_type = target_type.get_element_type()
        else:
            result_type = self.ctx.analyzer.get_expr_type(expr)
            elem_type = result_type.get_element_type() if result_type else None

        # Resolve IntLiteralType to BigInt (Python semantics)
        if isinstance(elem_type, IntLiteralType):
            elem_type = BIGINT
            if isinstance(result_type, ListType):
                result_type = ListType(BIGINT)

        # Use repeat_range for all list repeats (handles negative counts internally)
        elements = ", ".join(self.gen_expr_deref(e, elem_type) for e in expr.elements)
        cpp_elem_type = elem_type.to_cpp() if elem_type else "auto"
        range_expr = f"tpy::repeat_range<{cpp_elem_type}>({count}, {{{elements}}})"

        # Use unified from_range template for all range-constructible types
        cpp_type = result_type.to_cpp()
        return f"tpy::from_range<{cpp_type}>({range_expr})"

    def _gen_subscript(self, expr: TpySubscript) -> str:
        """Generate subscript code."""
        obj = self.gen_expr(expr.obj)
        if expr.obj.narrowed_optional_proven:
            obj = f"(*{obj})"
        obj_type = self.types.get_resolved_type(expr.obj)
        index_type = self.ctx.analyzer.get_expr_type(expr.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
        analyzed_obj_type = self.ctx.analyzer.get_expr_type(expr.obj)
        if (
            expr.needs_optional_runtime_check
            and isinstance(analyzed_obj_type, OptionalType)
            and not analyzed_obj_type.inner.is_value_type()
        ):
            if isinstance(expr.obj, TpyFieldAccess):
                subscript_obj = f"tpy::deref_optional({obj})"
            else:
                # For pointer-globals with wrapper storage, this yields raw `T*`.
                ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
                subscript_obj = f"tpy::deref_ptr({ptr_expr})"
        index_expr = self.gen_index_expr(subscript_obj, expr.index, index_type)

        # Use registry lookup for __getitem__
        cpp_template = self.builtins.get_type_method_template(obj_type, "__getitem__")
        if cpp_template:
            return expand_cpp_template(cpp_template, subscript_obj, index_expr)
        # Fallback for types without __getitem__ (e.g., str)
        return f"{subscript_obj}[{index_expr}]"

    def gen_index_expr(self, obj: str, index: TpyExpr, index_type: TpyType) -> str:
        """Generate index expression, handling negative indices with Python semantics."""
        is_neg, abs_val = self._is_negative_literal(index)
        if is_neg:
            # Negative literal: items[-1] -> items[tpy::__len__(items) - 1]
            # Use tpy::__len__() for universal support (std types and user records)
            return f"static_cast<int32_t>(tpy::__len__({obj}) - {abs_val})"

        index_expr = self.gen_expr_deref(index)
        if self.types.is_runtime_bigint(index, index_type):
            index_expr = f"{index_expr}.to_int32()"
        return index_expr

    def _is_negative_literal(self, expr: TpyExpr) -> tuple[bool, int]:
        """Check if expression is a negative integer literal.

        Returns (True, abs_value) if it's a negative literal, (False, 0) otherwise.
        """
        if isinstance(expr, TpyUnaryOp) and expr.op == "-":
            if isinstance(expr.operand, TpyIntLiteral):
                return (True, expr.operand.value)
        return (False, 0)

    def _gen_binop_from_result(self, binop_result: ResolvedBinop,
                               left: str, right: str) -> str:
        """Generate binary operation code from a ResolvedBinop.

        Applies wrappers to operands and substitutes into the method template.
        Handles is_reverse flag for reverse operators (__radd__, etc.).
        """
        wrapped_left = binop_result.left_wrapper.replace("{self}", left).replace("{expr}", left)
        wrapped_right = binop_result.right_wrapper.replace("{self}", right).replace("{expr}", right)
        cpp_template = binop_result.method.cpp_template
        if binop_result.is_reverse:
            return expand_cpp_template(cpp_template, wrapped_right, wrapped_left)
        else:
            return expand_cpp_template(cpp_template, wrapped_left, wrapped_right)

    def _gen_span_coercion(self, expr: TpyExpr, span_type: SpanType, gen_inner: str) -> str:
        """Generate std::span conversion for supported container types."""
        if isinstance(expr, TpyArrayLiteral):
            expected_array_type = ArrayType(span_type.element_type, len(expr.elements))
            array_expr = f"{expected_array_type.to_cpp()}{gen_inner}"
            return f"tpy::as_span({array_expr})"
        # gen_inner already generated, need to check if source was global
        if self.ctx.is_indirect_name(expr):
            gen_inner = f"(*{gen_inner})"
        return f"tpy::as_span({gen_inner})"

    def _convert_to_int32_arg(self, gen_expr: str, actual_type: TpyType, expected_type: TpyType, expr: TpyExpr) -> str:
        """Convert to Int32 when a runtime BigInt may be present."""
        if isinstance(expected_type, Int32Type):
            if isinstance(actual_type, BigIntType):
                return f"({gen_expr}).to_int32()"
            if isinstance(actual_type, IntLiteralType) and self.types.is_runtime_bigint(expr, actual_type):
                return f"({gen_expr}).to_int32()"
        return gen_expr
