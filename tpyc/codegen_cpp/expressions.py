"""
TurboPython Expression Code Generation

Generates C++ code from TurboPython expressions.
"""

from __future__ import annotations
from typing import Final, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType, BoolType, StrType, CharType,
    NamedType, PtrType, OwnType, OptionalType, NoneType, ArrayType, ListType, PendingListType,
    SpanType, TypeParamRef, ReadonlyType, unwrap_readonly, unwrap_optional_own, UnionType, VoidType, make_union, union_none_narrow,
    EnumType, IntEnumType,
    INT32, BIGINT, FLOAT, CHAR, VOID, is_protocol_type, is_any_str_type,
    ResolvedBinop
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySlice, TpySubscript, TpyCoerce
)
from ..prescan import match_is_none
from ..namespace import BindingKind
from .context import escape_cpp_string, escape_cpp_char, escape_cpp_name, qualified_cpp_name, expand_cpp_template

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .builtins import BuiltinGenerator
    from .protocols import ProtocolGenerator

from tpyc import modules as builtin_modules


class _Unset:
    """Sentinel distinguishing 'not provided' from explicit None."""
    __slots__ = ()


_UNSET: Final[_Unset] = _Unset()


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
        self.builtins.set_expr_generator(self.gen_expr, self.gen_expr_deref, self.gen_call_arg)

    def gen_expr_deref(self, expr: TpyExpr, target_type: TpyType = None) -> str:
        """Generate an expression, dereferencing globals.

        Use this when the underlying value is needed (e.g., method calls,
        operators, function arguments). For assignment targets, use gen_expr.
        """
        result = self.gen_expr(expr, target_type)
        is_narrowed = isinstance(expr, TpyName) and expr.name in self.ctx.narrowed_vars
        if self.ctx.is_indirect_name(expr) and not is_narrowed:
            result = f"(*{result})"
        # Value optionals are represented as std::optional<T> and must be
        # unwrapped when a concrete value is required.
        expr_type = self.types.get_resolved_type(expr)
        analyzed_type = self.ctx.get_expr_type(expr)
        # Also check the C++ declared type for variables whose sema type was
        # narrowed (e.g. inside `if x is not None:`). The sema type is the
        # narrowed inner type but the C++ variable is still std::optional<T>.
        cpp_declared_type = self._get_cpp_declared_type(expr)
        is_value_optional = (
            isinstance(expr_type, OptionalType) and not expr_type.uses_pointer_repr()
        ) or (
            cpp_declared_type is not None
            and isinstance(cpp_declared_type, OptionalType) and not cpp_declared_type.uses_pointer_repr()
        )
        if (
            target_type is not None
            and is_value_optional
            and not isinstance(target_type, OptionalType)
        ):
            # If sema already narrowed this expression to non-Optional, unwrap
            # without an extra runtime check. Otherwise keep checked dereference.
            if isinstance(analyzed_type, OptionalType):
                result = f"tpy::deref_optional_check({result})"
            else:
                result = f"(*{result})"
        return result

    def _get_cpp_declared_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the C++ declared type of a variable or field access.

        For names, checks codegen var_types and current_func_params.
        For field access (obj.field), resolves the field's declared type
        on the record, which may be Optional even when sema has narrowed it.
        """
        if isinstance(expr, TpyName):
            return self.ctx.var_types.get(expr.name) or self.ctx.current_func_params.get(expr.name)
        if isinstance(expr, TpyFieldAccess):
            return self._resolve_field_declared_type(expr)
        return None

    def _resolve_field_declared_type(self, expr: TpyFieldAccess) -> TpyType | None:
        """Resolve the declared type of a field on its record/object."""
        obj_type = self._get_cpp_declared_type(expr.obj)
        if obj_type is None:
            obj_type = self.ctx.get_expr_type(expr.obj)
        if obj_type is None:
            return None
        actual_type = unwrap_readonly(obj_type)
        if isinstance(actual_type, PtrType):
            actual_type = actual_type.pointee
        elif isinstance(actual_type, OwnType):
            actual_type = actual_type.wrapped
        elif isinstance(actual_type, OptionalType):
            if actual_type.inner.is_value_type():
                return None
            actual_type = actual_type.inner
        if isinstance(actual_type, NamedType) and actual_type.is_record:
            record = self.ctx.analyzer.registry.get_record_for_type(actual_type)
            if record:
                for f in record.fields:
                    if f.name == expr.field:
                        return f.type
        return None

    def _maybe_move(self, expr: TpyExpr, gen_code: str) -> str:
        """Wrap in std::move() or std::forward() if expr is a last-use of a movable local."""
        if (isinstance(expr, TpyName)
                and expr.name in self.ctx.movable_locals
                and id(expr) in self.ctx.analyzer.ctx.all_last_uses):
            tp_name = self.ctx.forwarding_params.get(expr.name)
            if tp_name is not None:
                return f"std::forward<{tp_name}>({gen_code})"
            return f"std::move({gen_code})"
        return gen_code

    def gen_call_arg(self, arg: TpyExpr, ptype: TpyType | None,
                     target_type: TpyType | None | _Unset = _UNSET) -> str:
        """Generate a call argument with deref and auto-move for Own[T] params.

        target_type overrides ptype as the hint passed to gen_expr_deref.
        Pass None explicitly to suppress the target hint (e.g. record method
        args where the resolved param type should only drive the move check,
        not literal coercion).
        """
        gen_arg = self.gen_expr_deref(arg, ptype if target_type is _UNSET else target_type)
        if ptype is not None and unwrap_optional_own(unwrap_readonly(ptype)) is not None:
            gen_arg = self._maybe_move(arg, gen_arg)
        return gen_arg

    def gen_expr(self, expr: TpyExpr, target_type: TpyType = None) -> str:
        """Generate an expression.

        Args:
            expr: The expression to generate
            target_type: Optional expected type (for implicit promotion)
        """
        if isinstance(expr, TpyIntLiteral):
            return self._gen_int_literal_value(expr.value, target_type)

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
            if isinstance(target_type, UnionType):
                return "std::monostate{}"
            return "nullptr"

        elif isinstance(expr, TpyCoerce):
            if expr.coercion.name == "int_literal_to_fixed_int" or isinstance(expr.expected_type, SpanType):
                inner_target = expr.expected_type
            else:
                inner_target = expr.actual_type
            gen_inner = self.gen_expr(expr.expr, inner_target)
            if isinstance(expr.expected_type, SpanType):
                return self._gen_span_coercion(expr.expr, expr.expected_type, gen_inner)
            # IntLiteralType may be runtime BigInt; sema records this on the coercion.
            if expr.coercion.name == "int_literal_to_fixed_int":
                return gen_inner
            # Coercions that call methods on the inner expression need dereferencing for globals
            if expr.coercion.name in ("record_to_ptr", "record_to_const_ptr",
                                      "upcast_to_ptr", "upcast_to_const_ptr",
                                      "bigint_to_fixed_int"):
                if self.ctx.is_indirect_name(expr.expr):
                    gen_inner = f"(*{gen_inner})"
            return expr.coercion.codegen(gen_inner, expr.actual_type, expr.expected_type, expr.context_kind)

        elif isinstance(expr, TpyStrLiteral):
            # If target type is Char and single char, output as char literal
            if isinstance(target_type, CharType) and len(expr.value) == 1:
                return f"'{escape_cpp_char(expr.value)}'"
            return f'"{escape_cpp_string(expr.value)}"'

        elif isinstance(expr, TpyName):
            # Union type narrowing: use the std::get-extracted local
            if expr.name in self.ctx.narrowed_vars:
                return self.ctx.narrowed_vars[expr.name]
            # self -> (*this) only in instance methods (self is implicit receiver, not a param)
            if expr.name == "self" and self.ctx.in_method and "self" not in self.ctx.current_func_params:
                return "(*this)"
            # Native global name substitution (Python name -> C/C++ name)
            # Skip if shadowed by a local variable
            if expr.name in self.ctx.native_global_names and expr.name not in self.ctx.local_scope_names:
                return self.ctx.native_global_names[expr.name]
            # Check if this is an imported variable from a user module
            if expr.name in self.ctx.user_imported_variables:
                # Don't qualify if shadowed by a local variable
                if expr.name in self.ctx.local_scope_names:
                    return escape_cpp_name(expr.name)
                # Check if redefined at top level
                if expr.name in self.ctx.top_level_decls:
                    decl_line = self.ctx.top_level_decls[expr.name]
                    # In a function (current_stmt_line == 0): always use local
                    # At top level: use local only if current line >= declaration line
                    if self.ctx.current_stmt_line == 0 or self.ctx.current_stmt_line >= decl_line:
                        return escape_cpp_name(expr.name)
                # Use qualified import reference (convert dotted name to C++ namespace)
                source_module, original_name = self.ctx.user_imported_variables[expr.name]
                return qualified_cpp_name(source_module, original_name)
            return escape_cpp_name(expr.name)

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

        elif isinstance(expr, TpyFString):
            return self._gen_fstring(expr)

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
            # Propagate isinstance narrowing to RHS of &&/||
            inline_facts = self._collect_inline_isinstance_facts(
                expr.left, true_branch=(expr.op == "&&"))
            saved = {}
            for var_name, inline_expr in inline_facts.items():
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = inline_expr
            right = self.gen_truthy_expr(expr.right)
            for var_name, prev in saved.items():
                if prev is not None:
                    self.ctx.narrowed_vars[var_name] = prev
                else:
                    self.ctx.narrowed_vars.pop(var_name, None)
            return f"({left} {expr.op} {right})"

        expr_type = self.types.get_resolved_type(expr)
        rendered = self.gen_expr(expr)
        # IntEnum truthiness: value 0 is falsy (like int)
        # Base Enum: always truthy (CPython behavior)
        if isinstance(expr_type, IntEnumType):
            cpp_underlying = expr_type.underlying_type.to_cpp()
            return f"(static_cast<{cpp_underlying}>({rendered}) != 0)"
        if isinstance(expr_type, EnumType):
            return "true"
        if isinstance(expr_type, OptionalType) and not expr_type.uses_pointer_repr():
            return f"tpy::is_truthy({rendered})"
        # Types with __bool__() or __len__() fallback (user-defined and builtin containers)
        record = self.ctx.analyzer.registry.get_record_for_type(expr_type)
        if record:
            if self.ctx.is_indirect_name(expr):
                rendered = f"(*{rendered})"
            if record.get_method_overloads("__bool__"):
                return f"tpy::__bool__({rendered})"
            if record.get_method_overloads("__len__"):
                return f"(tpy::__len__({rendered}) != 0)"
        return rendered

    def _gen_binop(self, expr: TpyBinOp, target_type: TpyType | None) -> str:
        """Generate binary operation code."""
        # For pure literal binops without a fixed-int context, emit the computed
        # literal directly to preserve Python semantics for large intermediates.
        analyzed_type = self.ctx.analyzer.get_expr_type(expr)
        if (
            target_type is None
            and isinstance(analyzed_type, IntLiteralType)
            and analyzed_type.value is not None
            and not self.types.involves_variables(expr)
        ):
            resolved = self.types.get_resolved_type(expr)
            bigint_target = resolved if isinstance(resolved, BigIntType) else None
            return self._gen_int_literal_value(analyzed_type.value, bigint_target)

        # First pass: get raw types to detect fixed-int operands
        left_raw = self.types.get_resolved_type(expr.left)
        right_raw = self.types.get_resolved_type(expr.right)
        # If one operand is a FixedIntType, resolve literals as that type (not BigInt)
        fixed_context = target_type if isinstance(target_type, FixedIntType) else None
        if isinstance(left_raw, FixedIntType):
            fixed_context = left_raw
        elif isinstance(right_raw, FixedIntType):
            fixed_context = right_raw
        left_type = self.types.get_resolved_type(expr.left, fixed_context)
        right_type = self.types.get_resolved_type(expr.right, fixed_context)

        # Handle 'in' and 'not in' operators
        if expr.op in ("in", "not in"):
            left = self.gen_expr(expr.left)
            right = self.gen_expr(expr.right)
            # Dereference globals for .begin()/.end() calls
            if self.ctx.is_indirect_name(expr.right):
                right = f"(*{right})"
            right_resolved = self.types.get_resolved_type(expr.right)
            if is_any_str_type(right_resolved):
                # String contains: use .find() (works for both std::string and string_view)
                find_expr = f"({right}.find({left}) != std::string::npos)"
            else:
                # Collection: use std::find
                find_expr = f"(std::find({right}.begin(), {right}.end(), {left}) != {right}.end())"
            if expr.op == "not in":
                return f"(!{find_expr})"
            return find_expr

        # Identity operators (is / is not) -- nullable comparison
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
            # Union types with NoneType member: std::holds_alternative<std::monostate>
            union_expr = None
            if isinstance(left_type, UnionType) and isinstance(expr.right, TpyNoneLiteral):
                union_expr = expr.left
            elif isinstance(right_type, UnionType) and isinstance(expr.left, TpyNoneLiteral):
                union_expr = expr.right
            if union_expr is not None:
                val = self.gen_expr(union_expr)
                if self.ctx.is_indirect_name(union_expr):
                    val = f"(*{val})"
                check = f"std::holds_alternative<std::monostate>({val})"
                if expr.op == "is":
                    return f"({check})"
                else:
                    return f"(!{check})"
            # Fallback: pointer comparison
            cpp_op = "==" if expr.op == "is" else "!="
            left = self.gen_expr(expr.left)
            right = self.gen_expr(expr.right)
            return f"({left} {cpp_op} {right})"

        # Logical operators - generate C++ directly.
        if expr.op in ("&&", "||"):
            left = self.gen_expr_deref(expr.left)
            # Propagate isinstance narrowing to RHS of && (like short-circuit eval).
            # For &&, LHS true-facts apply; for ||, LHS false-facts apply.
            inline_facts = self._collect_inline_isinstance_facts(
                expr.left, true_branch=(expr.op == "&&"))
            saved = {}
            for var_name, inline_expr in inline_facts.items():
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = inline_expr
            right = self.gen_expr_deref(expr.right)
            for var_name, prev in saved.items():
                if prev is not None:
                    self.ctx.narrowed_vars[var_name] = prev
                else:
                    self.ctx.narrowed_vars.pop(var_name, None)
            return f"({left} {expr.op} {right})"

        # Comparison operators - generate C++ directly.
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            left_target = None
            right_target = None

            if expr.optional_safe_eq:
                # None-safe ==/!=: let C++ std::optional<T> handle None comparison.
                # Only unwrap operands that flow analysis has proven non-None.
                left_analyzed = self.ctx.get_expr_type(expr.left)
                right_analyzed = self.ctx.get_expr_type(expr.right)
                if isinstance(left_type, OptionalType) and not left_type.uses_pointer_repr():
                    if not isinstance(left_analyzed, OptionalType):
                        left_target = left_type.inner
                    elif not (isinstance(right_type, OptionalType) and not right_type.uses_pointer_repr()):
                        right_target = left_type.inner
                if isinstance(right_type, OptionalType) and not right_type.uses_pointer_repr():
                    if not isinstance(right_analyzed, OptionalType):
                        right_target = right_type.inner
                    elif not (isinstance(left_type, OptionalType) and not left_type.uses_pointer_repr()):
                        left_target = right_type.inner
            else:
                # Ordering operators / non-Optional: unwrap with runtime check
                if isinstance(left_type, OptionalType) and not left_type.uses_pointer_repr():
                    left_target = left_type.inner
                if isinstance(right_type, OptionalType) and not right_type.uses_pointer_repr():
                    right_target = right_type.inner

            # When comparing Char with string literal, output literal as char.
            # Skip for Optional sides in None-safe eq (would cause unwrapping).
            if left_target is None and isinstance(right_type, CharType):
                if not (expr.optional_safe_eq and isinstance(left_type, OptionalType)):
                    left_target = CHAR
            if right_target is None and isinstance(left_type, CharType):
                if not (expr.optional_safe_eq and isinstance(right_type, OptionalType)):
                    right_target = CHAR
            # Use gen_expr_deref for pointer-locals/globals (T* needs dereferencing)
            left = self.gen_expr_deref(expr.left, left_target)
            right = self.gen_expr_deref(expr.right, right_target)

            # BigInt has no implicit conversion to/from double in C++, so
            # mixed BigInt/float comparisons need an explicit cast (mirroring
            # Python's int-to-float promotion for comparisons).
            left_cmp = left_target if left_target is not None else left_type
            right_cmp = right_target if right_target is not None else right_type
            if isinstance(left_cmp, BigIntType) and isinstance(right_cmp, FloatType):
                left = f"static_cast<double>({left})"
            elif isinstance(right_cmp, BigIntType) and isinstance(left_cmp, FloatType):
                right = f"static_cast<double>({right})"

            # IntEnum coercion: cast enum operand(s) to underlying type
            if expr.int_enum_coercion:
                underlying_cpp = self.types.type_to_cpp(expr.int_enum_coercion.underlying_type)
                if isinstance(left_type, IntEnumType):
                    left = f"static_cast<{underlying_cpp}>({left})"
                if isinstance(right_type, IntEnumType):
                    right = f"static_cast<{underlying_cpp}>({right})"
            return f"({left} {expr.op} {right})"

        # Optimization: IntLiteral op IntLiteral with Int32 target -> direct Int32 arithmetic
        # This avoids unnecessary BigInt heap allocations
        # Use analyzer types for this check - analyzer returns IntLiteralType for all-literal
        # expressions (including nested binops like 2+3), while get_resolved_type returns BigInt
        # NOTE: Must also check operands aren't variables (loop vars have IntLiteralType but aren't literals)
        left_analyzer_type = self.ctx.analyzer.get_expr_type(expr.left)
        right_analyzer_type = self.ctx.analyzer.get_expr_type(expr.right)
        left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
        right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)
        if (isinstance(target_type, FixedIntType) and left_is_literal and right_is_literal):
            # Pass target_type to handle nested binops like 1 + (2 + 3)
            left = self.gen_expr(expr.left, target_type)
            right = self.gen_expr(expr.right, target_type)
            # Use registry to get the fixed-int binary operator
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                cpp_template = self.builtins.get_type_method_template(target_type, method_name)
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
                left = self._convert_to_fixed_int_arg(left, left_actual, param_type, expr.left)
            else:
                # left is {self} (receiver), right is {0} (argument)
                left = self.gen_expr_deref(expr.left, receiver_type)
                right = self.gen_expr_deref(expr.right, param_type)
                # Convert argument if needed (e.g., IntLiteralType that's actually BigInt)
                right_actual = self.types.get_resolved_type(expr.right, param_type)
                right = self._convert_to_fixed_int_arg(right, right_actual, param_type, expr.right)
            # IntEnum coercion: cast enum operand(s) to underlying type
            if expr.int_enum_coercion:
                underlying_cpp = self.types.type_to_cpp(expr.int_enum_coercion.underlying_type)
                if isinstance(left_type, IntEnumType):
                    left = f"static_cast<{underlying_cpp}>({left})"
                if isinstance(right_type, IntEnumType):
                    right = f"static_cast<{underlying_cpp}>({right})"
            # C++ can't deduce template params from bare initializer lists,
            # so array literal operands need explicit std::vector<T>{...} prefix
            if isinstance(receiver_type, ListType):
                cpp_type = receiver_type.to_cpp()
                if isinstance(expr.left, TpyArrayLiteral):
                    left = f"{cpp_type}{left}"
                if isinstance(expr.right, TpyArrayLiteral):
                    right = f"{cpp_type}{right}"
            # Generate binop using helper (handles wrappers and is_reverse)
            result = self._gen_binop_from_result(binop_result, left, right)
            # Wrap in parens to avoid precedence issues with cout << and other operators
            return f"({result})"

        # Fallback for IntLiteral + IntLiteral using configured default int type
        # (explicit fixed-int contexts are handled earlier).
        if isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType):
            default_int = self.ctx.analyzer.ctx.default_int_type
            left = self.gen_expr(expr.left, default_int)
            right = self.gen_expr(expr.right, default_int)
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

        # Record types with dunder operators - use generated C++ operator
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
        if (
            expr.op == "-"
            and isinstance(operand_type, IntLiteralType)
            and isinstance(expr.operand, TpyIntLiteral)
        ):
            # Keep literal negation as a plain constant to avoid emitting
            # checked fixed-int runtime helpers for compile-time literals.
            return self._gen_int_literal_value(-expr.operand.value, target_type)
        resolved_operand_type = self.types.get_resolved_type(expr.operand)
        unary_target = target_type
        if isinstance(resolved_operand_type, OptionalType) and not resolved_operand_type.uses_pointer_repr():
            unary_target = resolved_operand_type.inner
        operand = self.gen_expr_deref(expr.operand, unary_target)

        # Use resolved unary op from sema
        if unaryop_result := expr.resolved_unaryop:
            return expand_cpp_template(unaryop_result.method.cpp_template, operand)

        # IntEnum: unary negation via static_cast
        if isinstance(operand_type, IntEnumType) and expr.op == "-":
            underlying_cpp = self.types.type_to_cpp(operand_type.underlying_type)
            return f"(-static_cast<{underlying_cpp}>({operand}))"

        # Fallback for IntLiteralType (not in module system)
        if isinstance(operand_type, IntLiteralType):
            return f"({expr.op}{operand})"

        raise RuntimeError(f"No codegen for unary operator {expr.op} with {operand_type}")

    def _collect_inline_isinstance_facts(
        self, expr: TpyExpr, true_branch: bool,
    ) -> dict[str, str]:
        """Collect inline std::get expressions for isinstance narrowing in conditions.

        For && RHS (true_branch=True): isinstance(v, A) means v is A.
        For || RHS (true_branch=False): isinstance(v, A) means v is NOT A.
        Returns {var_name: inline_get_expr} for concrete (non-union) types only.
        """
        facts: dict[str, TpyType] = {}
        self._extract_isinstance_facts(expr, true_branch, facts)
        result: dict[str, str] = {}
        for var_name, narrowed_type in facts.items():
            if isinstance(narrowed_type, (UnionType, NoneType)):
                continue
            cpp_type = self.types.type_to_cpp(narrowed_type)
            var_ref = var_name
            if var_name in self.ctx.narrowed_vars:
                var_ref = self.ctx.narrowed_vars[var_name]
            elif self.ctx.is_indirect_name(TpyName(var_name)):
                var_ref = f"(*{var_name})"
            result[var_name] = f"std::get<{cpp_type}>({var_ref})"
        return result

    def _extract_isinstance_facts(
        self, expr: TpyExpr, true_branch: bool, facts: dict[str, TpyType],
    ) -> None:
        """Walk expression tree to collect isinstance type facts."""
        if isinstance(expr, TpyCall) and expr.isinstance_var and expr.isinstance_type:
            if true_branch:
                facts[expr.isinstance_var] = expr.isinstance_type
            else:
                # False branch: compute remaining union members
                var_type = self.ctx.get_expr_type(expr.args[0])
                if isinstance(var_type, UnionType):
                    remaining = [m for m in var_type.members if m != expr.isinstance_type]
                    if remaining:
                        facts[expr.isinstance_var] = (
                            remaining[0] if len(remaining) == 1 else make_union(*remaining))
        elif (match := match_is_none(expr)) is not None:
            # is None / is not None on union types
            name, is_not_none = match
            name_expr = expr.left if isinstance(expr.left, TpyName) else expr.right
            var_type = self.ctx.get_expr_type(name_expr)
            if isinstance(var_type, UnionType) and var_type.has_none_member():
                non_none_type, none_type = union_none_narrow(var_type)
                if (is_not_none and true_branch) or (not is_not_none and not true_branch):
                    facts[name] = non_none_type
                else:
                    facts[name] = none_type
        elif isinstance(expr, TpyUnaryOp) and expr.op == "!":
            self._extract_isinstance_facts(expr.operand, not true_branch, facts)
        elif isinstance(expr, TpyBinOp) and expr.op == "&&":
            self._extract_isinstance_facts(expr.left, true_branch, facts)
            if true_branch:
                self._extract_isinstance_facts(expr.right, True, facts)
        elif isinstance(expr, TpyBinOp) and expr.op == "||":
            if not true_branch:
                self._extract_isinstance_facts(expr.left, False, facts)
                self._extract_isinstance_facts(expr.right, False, facts)

    def _gen_int_literal_value(self, v: int, target_type: TpyType | None) -> str:
        """Emit an integer literal, wrapping in BigInt constructor if needed."""
        if isinstance(target_type, BigIntType):
            if -2**31 <= v <= 2**31 - 1:
                return f"tpy::BigInt({v})"
            if -2**63 <= v <= 2**63 - 1:
                return f"tpy::BigInt(static_cast<int64_t>({v}LL))"
            return f'tpy::BigInt::from_str("{v}")'
        return str(v)

    def _gen_call(self, expr: TpyCall) -> str:
        """Generate function call code."""
        # isinstance(x, T) -> std::holds_alternative<CppT>(x)
        if expr.isinstance_var is not None and expr.isinstance_type is not None:
            cpp_type = self.types.type_to_cpp(expr.isinstance_type)
            var_name = expr.isinstance_var
            if var_name in self.ctx.narrowed_vars:
                var_name = self.ctx.narrowed_vars[var_name]
            name_node = TpyName(var_name)
            var_ref = self.gen_expr_deref(name_node) if self.ctx.is_indirect_name(name_node) else var_name
            return f"std::holds_alternative<{cpp_type}>({var_ref})"
        # Enum value lookup: Color(0) -> tpy::EnumUtil<Color>::from_value(0)
        if expr.enum_from_value is not None:
            enum_type = expr.enum_from_value
            cpp_type = enum_type.to_cpp()
            underlying_cpp = enum_type.underlying_type.to_cpp()
            arg = self.gen_expr(expr.args[0])
            # BigInt needs checked conversion to the underlying type
            arg_type = self.types.get_resolved_type(expr.args[0])
            if isinstance(arg_type, BigIntType):
                arg = f"({arg}).to_fixed_check<{underlying_cpp}>()"
            return f"tpy::EnumUtil<{cpp_type}>::from_value({arg})"
        # Enum try_parse: try_parse(Color, "Red") -> tpy::EnumUtil<Color>::try_parse("Red")
        if expr.enum_try_parse is not None:
            enum_type = expr.enum_try_parse
            cpp_type = enum_type.to_cpp()
            arg = self.gen_expr(expr.args[1])
            return f"tpy::EnumUtil<{cpp_type}>::try_parse({arg})"
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
                    arg_type = self.ctx.get_expr_type(arg)
                    # Optional non-value: keep native representation (T* or std::optional<T>)
                    if isinstance(arg_type, OptionalType) and arg_type.uses_pointer_repr():
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

                # @dynamic protocol params: wrap concrete args in temp adapter
                unwrapped_ptype = unwrap_readonly(resolved_ptype)
                if is_protocol_type(unwrapped_ptype):
                    protocol_info = self.ctx.analyzer.registry.get_protocol(unwrapped_ptype.name)
                    if protocol_info and protocol_info.is_dynamic:
                        arg_type = self.ctx.get_expr_type(arg)
                        if is_protocol_type(arg_type):
                            # Already erased (dynamic protocol var) -- dereference pointer-local
                            gen_args.append(self.gen_expr_deref(arg, resolved_ptype))
                        elif self.protocols.directly_implements_dynamic(arg_type, unwrapped_ptype.name):
                            # Direct inheritance -- implicit upcast to Base&, no adapter
                            if self.ctx.is_temporary_expr(arg):
                                # Rvalue can't bind to non-const lvalue ref -- materialize
                                concrete_cpp = self.types.type_to_cpp(arg_type)
                                arg_expr = self.gen_expr(arg, arg_type)
                                temp_name = self.ctx.temps.create_typed(concrete_cpp, arg_expr, brace_init=True)
                                gen_args.append(temp_name)
                            else:
                                gen_args.append(self.gen_call_arg(arg, resolved_ptype))
                        else:
                            # Structural conformance -- wrap in adapter
                            concrete_cpp = self.types.type_to_cpp(arg_type)
                            proto_name = unwrapped_ptype.name
                            arg_expr = self.gen_expr_deref(arg, arg_type)
                            if self.ctx.is_temporary_expr(arg):
                                # Rvalue: owning adapter (value must live in the temp)
                                adapter_type = self.protocols.get_dynamic_adapter_type(proto_name, concrete_cpp)
                            else:
                                # Lvalue: ref adapter (zero-copy, mutations visible)
                                adapter_type = self.protocols.get_dynamic_ref_adapter_type(proto_name, concrete_cpp)
                            temp_name = self.ctx.temps.create_typed(adapter_type, arg_expr, brace_init=True)
                            gen_args.append(temp_name)
                        continue

                # Optional non-value params are T* / const T* -- pass raw pointer
                actual_ptype = unwrap_readonly(resolved_ptype)
                if isinstance(actual_ptype, OptionalType) and actual_ptype.uses_pointer_repr():
                    if isinstance(arg, TpyNoneLiteral):
                        gen_args.append("nullptr")
                    elif self.ctx.is_indirect_name(arg):
                        # Already a T* pointer-local/global -- pass as-is
                        gen_args.append(self.gen_expr(arg, resolved_ptype))
                    elif isinstance(self.ctx.get_expr_type(arg), OptionalType):
                        arg_gen = self.gen_expr(arg, resolved_ptype)
                        if isinstance(arg, TpyFieldAccess):
                            # Field access produces std::optional<T>, convert to T*
                            gen_args.append(f"tpy::optional_to_ptr({arg_gen})")
                        else:
                            # Expression already produces T* (e.g. function returning Optional)
                            gen_args.append(arg_gen)
                    else:
                        # Lvalue reference -- take address
                        gen_args.append(f"&({self.gen_expr(arg, resolved_ptype)})")
                # Temporaries passed to mutable reference params need a temp variable
                # because C++ can't bind rvalue to non-const lvalue reference
                # TypeParamRef generates param_val_or_ref_t<T> which is T& for object types
                elif (resolved_ptype.is_ref_param() or isinstance(ptype, TypeParamRef)) and self.ctx.is_temporary_expr(arg):
                    init_expr = self.gen_expr(arg, resolved_ptype)
                    temp_name = self.ctx.temps.create(resolved_ptype, init_expr)
                    gen_args.append(temp_name)
                # Union params: wrap concrete member type in std::variant via a temp
                # so the lvalue reference can bind.
                elif isinstance(unwrap_readonly(resolved_ptype), UnionType):
                    arg_type = self.ctx.get_expr_type(arg)
                    cpp_decl = self._get_cpp_declared_type(arg)
                    already_union = (
                        isinstance(arg_type, UnionType)
                        or (cpp_decl is not None and isinstance(cpp_decl, UnionType))
                    )
                    if arg_type is not None and not already_union:
                        variant_cpp = self.types.type_to_cpp(unwrap_readonly(resolved_ptype))
                        arg_expr = self.gen_expr_deref(arg, arg_type)
                        arg_expr = self._maybe_move(arg, arg_expr)
                        temp_name = self.ctx.temps.create_typed(variant_cpp, arg_expr, brace_init=False)
                        gen_args.append(temp_name)
                    else:
                        gen_args.append(self.gen_call_arg(arg, resolved_ptype))
                else:
                    gen_args.append(self.gen_call_arg(arg, resolved_ptype))

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
                func_cpp_name = qualified_cpp_name(source_module, original_name)

            # For generic functions, always emit explicit type args to avoid C++ deduction issues
            # with tpy::param_val_or_ref_t<T> parameters
            if func_info.is_generic() and expr.inferred_type_args:
                type_args_str = ", ".join(self.types.type_to_cpp(t) for t in expr.inferred_type_args)
                return f"{func_cpp_name}<{type_args_str}>({', '.join(gen_args)})"
            return f"{func_cpp_name}({', '.join(gen_args)})"
        # Generic type instantiation (e.g., Container[T, N]())
        if expr.call_type is not None:
            # Pointer null constructors: Ptr[T]() / ReadOnlyPtr[T]() -> typed nullptr
            if isinstance(expr.call_type, PtrType) and not expr.args:
                cpp_type = self.types.type_to_cpp(expr.call_type)
                return f"static_cast<{cpp_type}>(nullptr)"
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
            # Look up resolved init params for auto-move on Own[T] params
            init_params = []
            call_type = expr.call_type
            record_name = call_type.name if isinstance(call_type, NamedType) else None
            if record_name:
                rec_info = self.ctx.analyzer.registry.get_record(record_name)
                if rec_info:
                    init_info = rec_info.get_method("__init__")
                    if init_info and expr.resolved_function_info:
                        init_params = expr.resolved_function_info.params
            gen_args = []
            for i, a in enumerate(expr.args):
                ptype = init_params[i].type if i < len(init_params) else None
                # None literals need param type to decide nullptr vs std::nullopt
                t_type = ptype if isinstance(a, TpyNoneLiteral) and ptype else expr.call_type
                gen_args.append(self.gen_call_arg(a, ptype, target_type=t_type))
            args = ", ".join(gen_args)
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
                # Optional non-value params are T* / const T* -- same logic as function calls
                actual_ptype = unwrap_readonly(ptype) if ptype else ptype
                if isinstance(actual_ptype, OptionalType) and actual_ptype.uses_pointer_repr():
                    if isinstance(a, TpyNoneLiteral):
                        gen_args.append("nullptr")
                    elif self.ctx.is_indirect_name(a):
                        gen_args.append(self.gen_expr(a, ptype))
                    elif isinstance(self.ctx.get_expr_type(a), OptionalType):
                        arg_gen = self.gen_expr(a, ptype)
                        if isinstance(a, TpyFieldAccess):
                            gen_args.append(f"tpy::optional_to_ptr({arg_gen})")
                        else:
                            gen_args.append(arg_gen)
                    else:
                        gen_args.append(f"&({self.gen_expr(a, ptype)})")
                else:
                    gen_args.append(self.gen_call_arg(a, ptype))
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
                return f"{qualified_cpp_name(source_module, original_name)}({args})"
            return f"{expr.func}({args})"
        args = ", ".join(self.gen_expr(a) for a in expr.args)
        return f"{expr.func}({args})"

    def _gen_method_call(self, expr: TpyMethodCall) -> str:
        """Generate method call code."""
        if expr.resolved_function_info:
            params = expr.resolved_function_info.params
            gen_args = [self.gen_call_arg(arg, params[i].type if i < len(params) else None)
                        for i, arg in enumerate(expr.args)]
            args = ", ".join(gen_args)
        else:
            args = ", ".join(self.gen_expr_deref(a) for a in expr.args)

        # Handle user module function calls: module.func() -> ::tpy_user::module::func()
        if expr.user_module_call is not None:
            fi = expr.resolved_function_info
            if fi and (fi.is_native_import or fi.is_extern_c):
                func_name = fi.native_name or fi.name
                return f"{qualified_cpp_name(expr.user_module_call, func_name)}({args})"
            # Emit explicit template args for generic user-module calls
            if fi and fi.is_generic() and expr.inferred_type_args:
                type_args_str = ", ".join(self.types.type_to_cpp(t) for t in expr.inferred_type_args)
                return f"{qualified_cpp_name(expr.user_module_call, expr.method)}<{type_args_str}>({args})"
            return f"{qualified_cpp_name(expr.user_module_call, expr.method)}({args})"

        # Handle builtin module function/type calls (e.g., time.time() or t.Int32() with import tpy as t)
        if expr.builtin_module_call is not None:
            module_name = expr.builtin_module_call
            # try_parse(Color, "Red") from tpy
            if module_name == "tpy" and expr.method == "try_parse":
                fi = expr.resolved_function_info
                enum_type = fi.return_type.inner
                cpp_type = enum_type.to_cpp()
                arg = self.gen_expr(expr.args[1])
                return f"tpy::EnumUtil<{cpp_type}>::try_parse({arg})"
            # copy() from tpy -- deref the argument to get the value
            if module_name == "tpy" and expr.method == "copy":
                arg = expr.args[0]
                arg_type = self.ctx.get_expr_type(arg)
                if isinstance(arg_type, OptionalType) and arg_type.uses_pointer_repr():
                    return self.gen_expr(arg)
                return self.gen_expr_deref(arg)
            # Special-handling functions with cpp_template resolved by sema
            fi = expr.resolved_function_info
            if fi and fi.special_handling and fi.cpp_template:
                gen_args = [self.gen_expr_deref(arg, p.type)
                            for arg, p in zip(expr.args, fi.params)]
                return fi.cpp_template.format(*gen_args)
            module_info = self.ctx.analyzer.registry.get_module(module_name)
            if module_info and expr.method in module_info.functions:
                from ..parse import TpyCall
                temp_call = TpyCall(func=expr.method, args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                temp_call.resolved_function_info = expr.resolved_function_info
                temp_call.inferred_type_args = expr.inferred_type_args
                return self.builtins.gen_builtin_function_overloads(temp_call, module_info.functions[expr.method])
            # Check for type constructor (e.g., tpy.Int32)
            qname = f"{module_name}.{expr.method}"
            if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                if record_info.constructors and not record_info.type_params:
                    from ..parse import TpyCall
                    temp_call = TpyCall(func=expr.method, args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                    return self.builtins.gen_builtin_constructor(temp_call, record_info)

        # Check for inherited builtin method with cpp_template first
        # This must be checked before the self.method() shortcut because
        # inherited builtin methods need the cpp_template substitution
        if expr.resolved_function_info:
            method_info = expr.resolved_function_info
            if method_info.cpp_template:
                # Static method on builtin type — no receiver, just args
                if expr.is_static_call:
                    gen_args = [self.builtins._gen_expr_deref(arg, ptype)
                                for arg, (_, ptype) in zip(expr.args, method_info.params)]
                    return method_info.cpp_template.format(*gen_args)
                # For self.inherited_method(), use (*this) as the receiver
                if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
                    return self.builtins.gen_method_from_function_info("(*this)", expr.args, method_info)
                obj = self.gen_expr(expr.obj)
                obj, an = self._apply_assign_narrowing(expr.obj, obj)
                # Dereference Ptr-typed fields when method was resolved through deref chain
                is_ptr_deref = expr.deref_depth > 0 and self.types.get_resolved_type(expr.obj).is_pointer()
                method_obj = f"(*{obj})" if (self.ctx.is_indirect_name(expr.obj) and not an) or is_ptr_deref else obj
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
            class_name = expr.obj.name
            if expr.inferred_type_args:
                type_args_str = ", ".join(self.types.type_to_cpp(t) for t in expr.inferred_type_args)
                class_name = f"{class_name}<{type_args_str}>"
            return f"{class_name}::{expr.method}({args})"
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
                if module_name in self.ctx.analyzer.ctx.bare_module_imports:
                    module_info = self.ctx.analyzer.registry.get_module(module_name)
                    if module_info and expr.method in module_info.functions:
                        # Create a temp call for code generation
                        from ..parse import TpyCall
                        temp_call = TpyCall(func=expr.method, args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                        return self.builtins.gen_builtin_function_overloads(temp_call, module_info.functions[expr.method])
        obj = self.gen_expr(expr.obj)
        obj, is_assign_narrowed = self._apply_assign_narrowing(expr.obj, obj)
        obj_type = self.types.get_resolved_type(expr.obj)

        # Unwrap OwnType for method lookup - Own[T] behaves as T for method calls
        if isinstance(obj_type, OwnType):
            obj_type = obj_type.wrapped

        # Builtin methods with cpp_template (resolved by sema)
        if expr.resolved_function_info:
            method_info = expr.resolved_function_info
            if method_info.cpp_template:
                # Dereference for pointer-locals/globals (T*) and Ptr[T]-typed fields
                # Only dereference Ptr-typed when method was resolved through deref chain
                is_ptr_deref = expr.deref_depth > 0 and obj_type is not None and obj_type.is_pointer()
                needs_deref = (self.ctx.is_indirect_name(expr.obj) and not is_assign_narrowed) or is_ptr_deref
                method_obj = f"(*{obj})" if needs_deref else obj
                return self.builtins.gen_method_from_function_info(method_obj, expr.args, method_info)

        # User-defined record methods may need temp handling for TypeParamRef params
        # TypeParamRef generates param_val_or_ref_t<T> which is T& for object types
        # Temporaries can't bind to non-const lvalue reference
        if isinstance(obj_type, NamedType) and obj_type.is_user_record:
            record_info = self.ctx.analyzer.registry.get_record_for_type(obj_type)
            if record_info:
                method_info = record_info.get_method(expr.method)
                if method_info:
                    # Build type substitution: {"T": Int32} for Box[Int32]
                    type_subst = {}
                    if record_info.type_params and obj_type.type_args:
                        for param_name, arg_type in zip(record_info.type_params, obj_type.type_args):
                            type_subst[param_name] = arg_type
                    gen_args = []
                    resolved_params = expr.resolved_function_info.params if expr.resolved_function_info else []
                    for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, method_info.params)):
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
                            rptype = resolved_params[i].type if i < len(resolved_params) else ptype
                            # None literals need target type to decide nullptr vs std::nullopt
                            arg_target = rptype if isinstance(arg, TpyNoneLiteral) else None
                            gen_args.append(self.gen_call_arg(arg, rptype, target_type=arg_target))
                    args = ", ".join(gen_args)

        # Use -> for pointer-locals/globals (T*) and pointer-typed expressions
        # (OptionalType non-value expressions like function calls return T*)
        obj_type = self.ctx.get_expr_type(expr.obj)
        is_optional_ptr = isinstance(obj_type, OptionalType) and obj_type.uses_pointer_repr()
        deref_chain = ".__deref__()" * expr.deref_depth
        # Optional with runtime null check -- must come before deref fast path
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"tpy::deref_optional_check({obj}){deref_chain}.{expr.method}({args})"
            # For pointer-globals with wrapper storage, this yields raw `T*`.
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"tpy::deref_check({ptr_expr}){deref_chain}.{expr.method}({args})"
        # User-defined Deref: emit .__deref__() calls before method call
        is_narrowed = (isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.narrowed_vars) or is_assign_narrowed
        if deref_chain and obj_type and not obj_type.is_pointer():
            is_indirect = self.ctx.is_indirect_name(expr.obj) and not is_narrowed
            if is_indirect or is_optional_ptr:
                return f"{obj}->{deref_chain[1:]}.{expr.method}({args})"
            return f"{obj}{deref_chain}.{expr.method}({args})"
        if obj_type and obj_type.is_pointer():
            if expr.ptr_non_null:
                return f"{obj}->{expr.method}({args})"
            return f"tpy::deref_check({obj}).{expr.method}({args})"
        use_arrow = (self.ctx.is_indirect_name(expr.obj) and not is_narrowed) or is_optional_ptr
        accessor = "->" if use_arrow else "."
        # Use native method name if available (for @native/@native_c class methods)
        cpp_method = expr.resolved_function_info.native_name if expr.resolved_function_info and expr.resolved_function_info.native_name else expr.method
        return f"{obj}{accessor}{cpp_method}({args})"

    def _apply_assign_narrowing(self, expr_obj: TpyExpr, obj_code: str) -> tuple[str, bool]:
        """Apply inline std::get wrapping for assignment-narrowed union vars.

        Returns (possibly wrapped code, was_narrowed).
        """
        if isinstance(expr_obj, TpyName) and expr_obj.name in self.ctx.assign_narrowed_types:
            narrowed_type = self.ctx.assign_narrowed_types[expr_obj.name]
            cpp_type = self.types.type_to_cpp(narrowed_type)
            if self.ctx.is_indirect_name(expr_obj):
                return f"std::get<{cpp_type}>((*{expr_obj.name}))", True
            return f"std::get<{cpp_type}>({obj_code})", True
        return obj_code, False

    def _gen_field_access(self, expr: TpyFieldAccess) -> str:
        """Generate field access code."""
        cpp_field = escape_cpp_name(expr.field)
        # Handle self.field -> this->field (inside method)
        # Using this-> avoids shadowing issues when field name matches parameter name
        if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
            return f"this->{cpp_field}"

        # Check for module variable access (e.g., sys.argv) and enum member access
        if isinstance(expr.obj, TpyName):
            if self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.MODULE:
                    # Get actual module name (may differ from local name for aliased imports)
                    module_name = binding.import_source[0] if binding.import_source else expr.obj.name
                    module_info = self.ctx.analyzer.registry.get_module(module_name)
                    if module_info and expr.field in module_info.variables:
                        return module_info.variables[expr.field].cpp_expr

                # Enum type-level member access: Color.Red -> Color::Red
                if binding and binding.kind == BindingKind.ENUM:
                    enum_name = expr.obj.name
                    # For cross-module enums, use qualified name
                    if enum_name in self.ctx.user_imported_enums:
                        src_mod, original = self.ctx.user_imported_enums[enum_name]
                        enum_name = qualified_cpp_name(src_mod, original)
                    return f"{enum_name}::{cpp_field}"

        obj = self.gen_expr(expr.obj)
        # Assignment narrowing: inline std::get<T> for member access only
        obj, is_assign_narrowed = self._apply_assign_narrowing(expr.obj, obj)
        # Check if obj is a pointer type or global - use -> instead of .
        obj_type = self.ctx.get_expr_type(expr.obj)

        # Enum instance property access: c.name, c.value
        actual_obj_type = obj_type
        if isinstance(actual_obj_type, ReadonlyType):
            actual_obj_type = actual_obj_type.wrapped
        if isinstance(actual_obj_type, OwnType):
            actual_obj_type = actual_obj_type.wrapped
        if isinstance(actual_obj_type, EnumType):
            if expr.field == "name":
                cpp_type = actual_obj_type.to_cpp()
                return f"tpy::EnumUtil<{cpp_type}>::name({obj})"
            elif expr.field == "value":
                return f"static_cast<{actual_obj_type.underlying_type.to_cpp()}>({obj})"

        # Narrowed vars (from isinstance std::get) are direct references, not pointers
        is_narrowed = (isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.narrowed_vars) or is_assign_narrowed
        is_indirect = self.ctx.is_indirect_name(expr.obj) and not is_narrowed
        is_optional_ptr = isinstance(obj_type, OptionalType) and obj_type.uses_pointer_repr()
        deref_chain = ".__deref__()" * expr.deref_depth
        # Optional with runtime null check -- must come before deref fast path
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"tpy::deref_optional_check({obj}){deref_chain}.{cpp_field}"
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"tpy::deref_check({ptr_expr}){deref_chain}.{cpp_field}"
        # User-defined Deref: emit .__deref__() calls before field access
        if deref_chain and obj_type and not obj_type.is_pointer():
            if is_indirect or is_optional_ptr:
                # C++ var is a pointer (narrowed Optional) -- arrow then deref chain
                return f"{obj}->{deref_chain[1:]}.{cpp_field}"
            return f"{obj}{deref_chain}.{cpp_field}"
        if obj_type and obj_type.is_pointer():
            if is_indirect and not isinstance(obj_type, PtrType):
                # Global pointer wrapper needs deref first: Global<Ptr<T>> -> (*global)->field
                return f"(*{obj})->{cpp_field}"
            if expr.ptr_non_null:
                return f"{obj}->{cpp_field}"
            return f"tpy::deref_check({obj}).{cpp_field}"
        if is_indirect or is_optional_ptr:
            return f"{obj}->{cpp_field}"
        return f"{obj}.{cpp_field}"

    def _gen_array_literal(self, expr: TpyArrayLiteral, target_type: TpyType | None) -> str:
        """Generate array literal code."""
        # Some types need explicit element targeting (Array, Span)
        # Others handle implicit conversions (list, etc.)
        elem_target = None
        if target_type and target_type.needs_explicit_element_target():
            elem_target = target_type.get_element_type()
        # For list literals with Optional/Union element types, pass element target
        # so None generates std::nullopt instead of nullptr
        if elem_target is None and target_type:
            et = target_type.get_element_type()
            if isinstance(et, (OptionalType, UnionType)):
                elem_target = et
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
            count = f"{count}.to_fixed_check<int32_t>()"

        # Determine result type and element type
        if target_type is not None:
            result_type = target_type
            elem_type = target_type.get_element_type()
        else:
            result_type = self.ctx.get_expr_type(expr)
            elem_type = result_type.get_element_type() if result_type else None

        # Resolve IntLiteralType to configured default integer type.
        if isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
            if isinstance(result_type, ListType):
                result_type = ListType(elem_type)

        # Use repeat_range for all list repeats (handles negative counts internally)
        elements = ", ".join(self.gen_expr_deref(e, elem_type) for e in expr.elements)
        cpp_elem_type = elem_type.to_cpp() if elem_type else "auto"
        range_expr = f"tpy::repeat_range<{cpp_elem_type}>({count}, {{{elements}}})"

        # Use unified from_range template for all range-constructible types
        cpp_type = result_type.to_cpp()
        return f"tpy::from_range<{cpp_type}>({range_expr})"

    def _gen_subscript(self, expr: TpySubscript) -> str:
        """Generate subscript code."""
        # Enum name lookup: Color["Red"] -> tpy::EnumUtil<Color>::from_name("Red")
        if expr.enum_from_name is not None:
            cpp_type = expr.enum_from_name.to_cpp()
            index = self.gen_expr(expr.index)
            return f"tpy::EnumUtil<{cpp_type}>::from_name({index})"

        obj = self.gen_expr(expr.obj)

        # Slice: obj[start:stop]
        if isinstance(expr.index, TpySlice):
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
            return self._gen_slice(subscript_obj, expr.index)

        obj_type = self.types.get_resolved_type(expr.obj)
        index_type = self.ctx.analyzer.get_expr_type(expr.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
        analyzed_obj_type = self.ctx.get_expr_type(expr.obj)
        if (
            expr.needs_optional_runtime_check
            and isinstance(analyzed_obj_type, OptionalType)
            and analyzed_obj_type.uses_pointer_repr()
        ):
            if isinstance(expr.obj, TpyFieldAccess):
                subscript_obj = f"tpy::deref_optional_check({obj})"
            else:
                # For pointer-globals with wrapper storage, this yields raw `T*`.
                ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
                subscript_obj = f"tpy::deref_check({ptr_expr})"
        index_expr = self.gen_index_expr(expr.index, index_type)

        # Use registry lookup for __getitem__
        cpp_template = self.builtins.get_type_method_template(obj_type, "__getitem__")
        if cpp_template:
            return expand_cpp_template(cpp_template, subscript_obj, index_expr)
        # Fallback: operator[] (user records generate const operator[] from __getitem__)
        return f"{subscript_obj}[{index_expr}]"

    def gen_index_expr(self, index: TpyExpr, index_type: TpyType) -> str:
        """Generate index expression, converting BigInt indices to int32_t.

        The raw index (possibly negative) is passed through to the runtime
        helpers which handle normalization and bounds checking, matching CPython.
        """
        index_expr = self.gen_expr_deref(index)
        if not self._is_int_constant(index) and self.types.is_runtime_bigint(index, index_type):
            index_expr = f"{index_expr}.to_fixed_check<int32_t>()"
        return index_expr

    @staticmethod
    def _is_int_constant(expr: TpyExpr) -> bool:
        """Check if expression is a compile-time integer constant that fits in int32_t.

        Covers both `42` (TpyIntLiteral) and `-1` (TpyUnaryOp("-", TpyIntLiteral)).
        Only returns True when the value fits in int32_t, so large BigInt literals
        still get .to_fixed_check<int32_t>() instead of silent narrowing.
        """
        INT32_MIN = -(1 << 31)
        INT32_MAX = (1 << 31) - 1
        if isinstance(expr, TpyIntLiteral):
            return INT32_MIN <= expr.value <= INT32_MAX
        if isinstance(expr, TpyUnaryOp) and expr.op == "-" and isinstance(expr.operand, TpyIntLiteral):
            return INT32_MIN <= -expr.operand.value <= INT32_MAX
        return False


    def _gen_slice(self, obj: str, sl: TpySlice) -> str:
        """Generate string slice: tpy::str_slice(obj, start, stop)."""
        if sl.lower is not None:
            start = self._gen_slice_bound(sl.lower)
        else:
            start = "0"
        if sl.upper is not None:
            stop = self._gen_slice_bound(sl.upper)
        else:
            stop = "INT32_MAX"
        return f"tpy::str_slice({obj}, {start}, {stop})"

    def _gen_slice_bound(self, expr: TpyExpr) -> str:
        """Generate a slice bound expression, converting to int32_t if needed."""
        index_type = self.ctx.analyzer.get_expr_type(expr)
        code = self.gen_expr_deref(expr)
        if self.types.is_runtime_bigint(expr, index_type):
            code = f"{code}.to_fixed_check<int32_t>()"
        return code

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

    def _convert_to_fixed_int_arg(self, gen_expr: str, actual_type: TpyType, expected_type: TpyType, expr: TpyExpr) -> str:
        """Convert to the target FixedIntType when a runtime BigInt may be present."""
        if isinstance(expected_type, FixedIntType):
            cpp_t = expected_type.to_cpp()
            if isinstance(actual_type, BigIntType):
                if isinstance(expr, TpyIntLiteral):
                    return gen_expr
                return f"({gen_expr}).to_fixed_check<{cpp_t}>()"
            if isinstance(actual_type, IntLiteralType) and self.types.is_runtime_bigint(expr, actual_type):
                # IntLiterals are emitted as plain C++ integers, not BigInt objects
                return gen_expr
        return gen_expr

    def _gen_fstring(self, expr: TpyFString) -> str:
        """Generate std::format(...) for an f-string."""
        fmt_parts: list[str] = []
        raw_parts: list[str] = []  # without brace-escaping, for pure-literal path
        args: list[str] = []
        all_literal = True

        for part in expr.parts:
            if isinstance(part, str):
                escaped = escape_cpp_string(part)
                raw_parts.append(escaped)
                # Escape braces for std::format
                fmt_parts.append(escaped.replace("{", "{{").replace("}", "}}"))
            else:
                all_literal = False
                gen_arg = self.gen_expr_deref(part.expr)
                arg_type = unwrap_readonly(self.types.get_resolved_type(part.expr))
                has_spec = part.format_spec is not None
                conv = part.conversion

                if has_spec:
                    fmt_parts.append("{:" + part.format_spec + "}")
                else:
                    fmt_parts.append("{}")

                is_user_type = (
                    (isinstance(arg_type, NamedType) and not arg_type.is_protocol)
                    or isinstance(arg_type, TypeParamRef)
                )

                # !r conversion: always wrap with __repr__
                if conv == FSTRING_CONV_REPR:
                    gen_arg = f"tpy::__repr__({gen_arg})"
                # !s conversion on user types: wrap with __str__
                elif conv == FSTRING_CONV_STR and is_user_type:
                    gen_arg = f"tpy::__str__({gen_arg})"
                # Wrap args that need Python-compatible formatting
                elif isinstance(arg_type, BoolType):
                    if has_spec:
                        gen_arg = f"static_cast<int>({gen_arg})"
                    else:
                        gen_arg = f"tpy::bool_to_str({gen_arg})"
                elif isinstance(arg_type, FloatType) and not has_spec:
                    gen_arg = f"tpy::float_to_str({gen_arg})"
                elif self.types.is_runtime_bigint(part.expr, arg_type) and not has_spec:
                    gen_arg = f"({gen_arg}).to_string()"
                elif isinstance(arg_type, FixedIntType) and arg_type.bits == 8:
                    gen_arg = f"static_cast<int>({gen_arg})"
                elif isinstance(arg_type, EnumType):
                    gen_arg = f"static_cast<int>({gen_arg})"
                elif is_user_type:
                    gen_arg = f"tpy::__str__({gen_arg})"

                args.append(gen_arg)

        if all_literal:
            # Pure literal f-string -- use raw parts (no brace-escaping needed)
            return f'std::string("{"".join(raw_parts)}")'

        fmt_str = "".join(fmt_parts)
        args_str = ", ".join(args)
        return f'std::format("{fmt_str}", {args_str})'
