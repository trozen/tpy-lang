"""
TurboPython Expression Code Generation

Generates C++ code from TurboPython expressions.
"""

from __future__ import annotations
import io
from typing import Final, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type, BoolType, StrType, StrViewType, CharType,
    NamedType, PtrType, OwnType, OptionalType, NoneType, ArrayType, ListType, DictType, SetType,
    DictKeysViewType, DictValuesViewType, DictItemsViewType,
    PendingListType, ListRepeatType,
    SpanType, SpanIterType, TypeParamRef, ReadonlyType, unwrap_readonly, unwrap_optional_own, UnionType, VoidType, make_union, union_none_narrow,
    EnumType, IntEnumType, TupleType, FnType,
    INT32, BIGINT, FLOAT, CHAR, VOID, is_protocol_type, is_any_str_type, container_to_str_template,
    ResolvedBinop, get_covariant_params,
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyTupleLiteral, TupleElemCapture, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr,
    TpyLambda,
)
from ..prescan import match_is_none
from ..namespace import BindingKind
from .context import INDENT, escape_cpp_string, escape_cpp_char, escape_cpp_name, qualified_cpp_name, expand_cpp_template, qualify_native_name

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .builtins import BuiltinGenerator
    from .protocols import ProtocolGenerator

from tpyc import modules as builtin_modules


def _is_simple_lvalue(expr: TpyExpr) -> bool:
    """Check if expression is a variable or field access (safe to capture by ref).

    Excludes subscripts -- they may yield const refs (e.g. Span[readonly[T]])
    which can't bind to T&.
    """
    if isinstance(expr, TpyCoerce):
        return _is_simple_lvalue(expr.expr)
    if isinstance(expr, TpyName):
        return True
    if isinstance(expr, TpyFieldAccess):
        return _is_simple_lvalue(expr.obj)
    return False


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
        self.builtins.set_expr_generator(self.gen_expr, self.gen_expr_deref, self.gen_call_arg,
                                         self._get_cpp_declared_type)

    def _nullptr_for_optional(self, ptype: TpyType) -> str:
        """Generate the right nullptr expression for an Optional pointer-repr param.

        For static protocol params, C++ can't deduce T from bare nullptr,
        so we emit a typed null pointer: static_cast<std::nullptr_t*>(nullptr).
        For regular Optional types, plain nullptr suffices.
        """
        if self.protocols.is_static_protocol_param(unwrap_readonly(ptype)):
            return "static_cast<std::nullptr_t*>(nullptr)"
        return "nullptr"

    def _gen_protocol_arg(self, arg: TpyExpr, ptype: TpyType) -> str | None:
        """Generate argument for a nullable or multi-protocol static protocol param.

        Handles Optional[Protocol], protocol unions (required or nullable).
        Single required protocols are NOT handled here -- they go through normal
        gen_call_arg which applies deref and move correctly.
        """
        if ptype is None:
            return None
        if not self.protocols.is_static_protocol_param(ptype):
            return None
        infos = self.protocols.get_all_protocol_params([("_", ptype)])
        if not infos:
            return None
        info = infos[0]
        # Single required protocol: let normal gen_call_arg handle it
        if len(info.protocols) == 1 and not info.has_none:
            return None
        if info.has_none:
            if isinstance(arg, TpyNoneLiteral):
                return "static_cast<std::nullptr_t*>(nullptr)"
            if self.ctx.is_indirect_name(arg):
                return self.gen_expr(arg, ptype)
            if isinstance(self.ctx.get_expr_type(arg), OptionalType):
                arg_gen = self.gen_expr(arg, ptype)
                if isinstance(arg, TpyFieldAccess):
                    return f"::tpy::optional_to_ptr({arg_gen})"
                return arg_gen
            gen = self.gen_expr(arg, ptype)
            if self.ctx.is_temporary_expr(arg):
                arg_type = self.ctx.get_expr_type(arg)
                tmp = self.ctx.temps.create(arg_type, gen) if arg_type else self.ctx.temps.create_typed("auto", gen)
                return f"&({tmp})"
            return f"&({gen})"
        # Required multi-protocol union: pass by reference, template deduction works.
        return self.gen_expr_deref(arg)

    def _gen_optional_ptr_arg(self, arg: TpyExpr, ptype: TpyType) -> str | None:
        """Generate argument for a non-protocol Optional pointer-repr param, or None if not applicable."""
        actual = unwrap_readonly(ptype) if ptype else ptype
        if not (isinstance(actual, OptionalType) and actual.uses_pointer_repr()):
            return None
        if isinstance(arg, TpyNoneLiteral):
            return self._nullptr_for_optional(ptype)
        if self.ctx.is_indirect_name(arg):
            return self.gen_expr(arg, ptype)
        arg_type = self.ctx.get_expr_type(arg)
        if isinstance(arg_type, OptionalType):
            arg_gen = self.gen_expr(arg, ptype)
            if isinstance(arg, TpyFieldAccess):
                return f"::tpy::optional_to_ptr({arg_gen})"
            # std::optional<T> -> T* conversion (generic return passed to concrete param)
            if not arg_type.uses_pointer_repr():
                return f"::tpy::optional_to_ptr({arg_gen})"
            return arg_gen
        gen = self.gen_expr(arg, ptype)
        if self.ctx.is_temporary_expr(arg):
            arg_type = self.ctx.get_expr_type(arg)
            tmp = self.ctx.temps.create(arg_type, gen) if arg_type else self.ctx.temps.create_typed("auto", gen)
            return f"&({tmp})"
        return f"&({gen})"

    def _gen_union_arg(self, arg: TpyExpr, ptype: TpyType,
                       is_readonly_target: bool = False) -> str | None:
        """Generate argument for a union-typed param, or None if not applicable."""
        is_readonly_param = is_readonly_target or isinstance(ptype, ReadonlyType)
        ptype_union = unwrap_readonly(ptype) if ptype else ptype
        if not isinstance(ptype_union, UnionType):
            return None
        arg_type = self.ctx.get_expr_type(arg)
        cpp_decl = self._get_cpp_declared_type(arg)
        already_union = (
            isinstance(arg_type, UnionType)
            or (cpp_decl is not None and isinstance(cpp_decl, UnionType))
        )
        if ptype_union.uses_pointer_repr():
            if is_readonly_param:
                pv_cpp = self.types.type_to_cpp_const_ptr_variant(ptype_union)
            else:
                pv_cpp = self.types.type_to_cpp_ptr_variant(ptype_union)
            if isinstance(arg, TpyNoneLiteral):
                return f"{pv_cpp}{{std::monostate{{}}}}"
            if arg_type is not None and not already_union:
                arg_expr = self.gen_expr_deref(arg, arg_type)
                if self.ctx.is_rvalue_source(arg):
                    temp = self.ctx.temps.create(arg_type, arg_expr)
                    return f"{pv_cpp}{{&{temp}}}"
                return f"{pv_cpp}{{&({arg_expr})}}"
            # Mutable ptr-variant arg -> const ptr-variant param: explicit conversion
            if is_readonly_param and already_union:
                # Skip if the arg is isinstance-narrowed to a concrete type
                is_narrowed = isinstance(arg, TpyName) and arg.name in self.ctx.narrowed_vars
                if not is_narrowed:
                    arg_expr = self.gen_expr_deref(arg)
                    return f"::tpy::ptr_variant_to_const<{pv_cpp}>({arg_expr})"
            return None  # already a union -- use default gen_call_arg
        else:
            if arg_type is not None and not already_union:
                variant_cpp = self.types.type_to_cpp(ptype_union)
                arg_expr = self.gen_expr_deref(arg, arg_type)
                arg_expr = self._maybe_move(arg, arg_expr)
                return self.ctx.temps.create_typed(variant_cpp, arg_expr, brace_init=False)
            return None  # already a union -- use default gen_call_arg

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
        effective_target = target_type
        if isinstance(effective_target, OwnType):
            effective_target = effective_target.wrapped
        if (
            target_type is not None
            and is_value_optional
            and not isinstance(effective_target, OptionalType)
        ):
            # If sema already narrowed this expression to non-Optional, unwrap
            # without an extra runtime check. Otherwise keep checked dereference.
            if isinstance(analyzed_type, OptionalType):
                result = f"::tpy::deref_optional_check({result})"
            else:
                result = f"(*{result})"
        return result

    def _maybe_convert_opt_str_param(self, name: str, result: str,
                                      target_type: TpyType | None) -> str:
        """Convert Optional[str] param (optional<string_view>) to optional<string>
        when needed by the target type."""
        if target_type is None:
            return result
        declared = self.ctx.current_func_params.get(name)
        if not (isinstance(declared, OptionalType)
                and isinstance(declared.inner, StrType)):
            return result
        target = target_type
        if isinstance(target, OwnType):
            target = target.wrapped
        if isinstance(target, OptionalType) and isinstance(target.inner, StrType):
            return (f"{result} ? std::make_optional("
                    f"std::string(*{result})) : std::nullopt")
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

    def _maybe_unwrap_narrowed_optional(self, expr_obj: TpyExpr, obj: str, needs_deref: bool) -> str:
        """Unwrap narrowed value-Optional receivers.

        When sema has proven a std::optional<T> variable holds a value,
        the C++ variable is still optional -- dereference it with (*obj).
        Handles both simple names and field access expressions.
        """
        if needs_deref:
            return obj
        cpp_decl = self._get_cpp_declared_type(expr_obj)
        analyzed = self.ctx.get_expr_type(expr_obj)
        if (cpp_decl is not None
                and isinstance(cpp_decl, OptionalType) and not cpp_decl.uses_pointer_repr()
                and not isinstance(analyzed, OptionalType)):
            return f"(*{obj})"
        return obj

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

    def _gen_copy_expr(self, arg: TpyExpr) -> str:
        """Generate an explicit copy of arg as an rvalue for Own[T] ownership transfer."""
        arg_type = self.ctx.get_expr_type(arg)
        if isinstance(arg_type, OptionalType) and arg_type.uses_pointer_repr():
            return self.gen_expr(arg)
        # Pointer-variant union locals: visit variant and copy active member
        if isinstance(arg, TpyName) and arg.name in self.ctx.ptr_variant_locals:
            var_type = self.ctx.var_types.get(arg.name)
            if isinstance(var_type, UnionType) and var_type.uses_pointer_repr():
                val_cpp = self.types.type_to_cpp(var_type)
                return f"::tpy::to_value_variant<{val_cpp}>({arg.name})"
        arg_expr = self.gen_expr_deref(arg)
        # Record constructors are prvalues — already an rvalue, no copy needed
        if isinstance(arg, TpyCall) and self.ctx.analyzer.registry.get_record(arg.func):
            return arg_expr
        return f"{arg_type.to_cpp()}({arg_expr})"

    def _maybe_move(self, expr: TpyExpr, gen_code: str) -> str:
        """Wrap in std::move() if expr is a last-use of a movable local."""
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if (isinstance(inner, TpyName)
                and inner.name in self.ctx.movable_locals
                and id(inner) in self.ctx.analyzer.ctx.all_last_uses):
            return f"std::move({gen_code})"
        return gen_code

    def _gen_dynamic_protocol_arg(self, arg: TpyExpr, ptype: TpyType) -> str | None:
        """If ptype is a @dynamic protocol, return the wrapped arg expression. Otherwise None."""
        unwrapped_ptype = unwrap_readonly(ptype)
        if not is_protocol_type(unwrapped_ptype):
            return None
        protocol_info = self.ctx.analyzer.registry.get_protocol(unwrapped_ptype.name)
        if not protocol_info or not protocol_info.is_dynamic:
            return None
        arg_type = self.ctx.get_expr_type(arg)
        if is_protocol_type(arg_type):
            return self.gen_expr_deref(arg, ptype)
        elif self.protocols.directly_implements_dynamic(arg_type, unwrapped_ptype.name):
            if self.ctx.is_temporary_expr(arg):
                concrete_cpp = self.types.type_to_cpp(arg_type)
                arg_expr = self.gen_expr(arg, arg_type)
                return self.ctx.temps.create_typed(concrete_cpp, arg_expr, brace_init=True)
            else:
                return self.gen_call_arg(arg, ptype)
        else:
            concrete_cpp = self.types.type_to_cpp(arg_type)
            proto_name = unwrapped_ptype.name
            arg_expr = self.gen_expr_deref(arg, arg_type)
            if self.ctx.is_temporary_expr(arg):
                adapter_type = self.protocols.get_dynamic_adapter_type(proto_name, concrete_cpp)
            else:
                adapter_type = self.protocols.get_dynamic_ref_adapter_type(proto_name, concrete_cpp)
            return self.ctx.temps.create_typed(adapter_type, arg_expr, brace_init=True)

    def _gen_covariant_arg(self, arg: TpyExpr, ptype: TpyType) -> str | None:
        """If ptype requires covariant conversion, return the wrapped arg. Otherwise None."""
        if not isinstance(ptype, NamedType) or not ptype.is_user_record:
            return None
        arg_type = self.ctx.get_expr_type(arg)
        if not isinstance(arg_type, NamedType) or not arg_type.is_user_record:
            return None
        if arg_type.name != ptype.name or arg_type.type_args == ptype.type_args:
            return None
        record_info = self.ctx.analyzer.registry.get_record(arg_type.name)
        if not record_info:
            return None
        covariant = get_covariant_params(record_info)
        if not covariant:
            return None
        # Create a named temp of the target type from the moved source
        target_cpp = self.types.type_to_cpp(ptype)
        arg_expr = self.gen_expr_deref(arg, arg_type)
        arg_expr = self._maybe_move(arg, arg_expr)
        return self.ctx.temps.create_typed(target_cpp, arg_expr)

    def gen_call_arg(self, arg: TpyExpr, ptype: TpyType | None,
                     target_type: TpyType | None | _Unset = _UNSET,
                     inline_template: bool = False) -> str:
        """Generate a call argument with auto-move at last use for Own[T] params.

        target_type overrides ptype as the hint passed to gen_expr_deref.
        Pass None explicitly to suppress the target hint (e.g. record method
        args where the resolved param type should only drive the move check,
        not literal coercion).

        inline_template: when True, the callee is a cpp_template expansion
        (e.g. push_back), not a real C++ function with T&& param. The callee
        natively accepts lvalues, so the copy-into-temp + move is unnecessary
        for plain names and literals.
        """
        gen_arg = self.gen_expr_deref(arg, ptype if target_type is _UNSET else target_type)
        if ptype is not None:
            own = unwrap_optional_own(unwrap_readonly(ptype))
            if own is not None:
                moved = self._maybe_move(arg, gen_arg)
                if moved is gen_arg and _is_simple_lvalue(arg):
                    # cpp_template callees (push_back, insert, etc.) accept
                    # lvalues natively -- skip the redundant copy+move.
                    # Exclude str: locals are string_view but containers
                    # store string, so the copy is a needed conversion.
                    if inline_template and not is_any_str_type(own.wrapped):
                        return gen_arg
                    # For TpyCoerce: real conversions produce rvalue expressions that
                    # bind to T&& directly. Identity coercions (same C++ type) leave
                    # the expression as an lvalue and still need a copy-temp.
                    needs_copy = True
                    if isinstance(arg, TpyCoerce):
                        inner = arg.expr
                        while isinstance(inner, TpyCoerce):
                            inner = inner.expr
                        if isinstance(inner, TpyName) and not self.ctx.is_indirect_name(inner):
                            if gen_arg != escape_cpp_name(inner.name):
                                needs_copy = False  # real conversion -> rvalue
                    if needs_copy:
                        # Use explicit type instead of auto when Own[T]
                        # wrapping requires a conversion (e.g. string_view
                        # -> string): auto would deduce the source type.
                        if is_any_str_type(own.wrapped):
                            tmp = self.ctx.temps.create_typed(
                                own.wrapped.to_cpp(), gen_arg, brace_init=True)
                        else:
                            tmp = self.ctx.temps.create_typed("auto", gen_arg)
                        gen_arg = f"std::move({tmp})"
                else:
                    gen_arg = moved
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
            val = repr(expr.value)
            if isinstance(target_type, Float32Type):
                if val == "inf":
                    return "std::numeric_limits<float>::infinity()"
                if val == "-inf":
                    return "(-std::numeric_limits<float>::infinity())"
                if val == "nan":
                    return "std::numeric_limits<float>::quiet_NaN()"
                return val + "f"
            return val

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
            # Unwrap Optional to detect span coercion inside Optional[Span[T]].
            # Single-level unwrap suffices: Span is a value type, never wrapped in Own.
            coerce_target = expr.expected_type
            if isinstance(coerce_target, OptionalType) and isinstance(coerce_target.inner, SpanType):
                coerce_target = coerce_target.inner
            if expr.coercion.name in ("int_literal_to_fixed_int", "float_literal_to_float32") or isinstance(coerce_target, SpanType):
                inner_target = coerce_target
            else:
                inner_target = expr.actual_type
            gen_inner = self.gen_expr(expr.expr, inner_target)
            if isinstance(coerce_target, SpanType):
                return self._gen_span_coercion(expr.expr, coerce_target, gen_inner)
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
                # Use qualified import reference (convert dotted name to C++ namespace).
                # Pointer indirection for non-value-type globals is handled by
                # is_indirect_name() -> gen_expr_deref() at call sites.
                source_module, original_name = self.ctx.user_imported_variables[expr.name]
                return qualified_cpp_name(source_module, original_name)
            result = escape_cpp_name(expr.name)
            return self._maybe_convert_opt_str_param(expr.name, result, target_type)

        elif isinstance(expr, TpyBinOp):
            return self._gen_binop(expr, target_type)

        elif isinstance(expr, TpyChainedCompare):
            return self._gen_chained_compare(expr)

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

        elif isinstance(expr, TpyTupleLiteral):
            return self._gen_tuple_literal(expr, target_type)

        elif isinstance(expr, TpyDictLiteral):
            return self._gen_dict_literal(expr)

        elif isinstance(expr, TpySetLiteral):
            return self._gen_set_literal(expr)

        elif isinstance(expr, TpyListRepeat):
            return self._gen_list_repeat(expr, target_type)

        elif isinstance(expr, TpyListComprehension):
            return self._gen_list_comprehension(expr, target_type)

        elif isinstance(expr, TpyDictComprehension):
            return self._gen_dict_comprehension(expr)

        elif isinstance(expr, TpySetComprehension):
            return self._gen_set_comprehension(expr)

        elif isinstance(expr, TpyGeneratorExpression):
            return self._gen_generator_expression(expr)

        elif isinstance(expr, TpySubscript):
            return self._gen_subscript(expr)

        elif isinstance(expr, TpyFString):
            return self._gen_fstring(expr)

        elif isinstance(expr, TpyIfExpr):
            return self._gen_if_expr(expr, target_type)

        elif isinstance(expr, TpyNamedExpr):
            return self._gen_named_expr(expr)

        elif isinstance(expr, TpyLambda):
            return self._gen_lambda(expr)

        return "/* unknown expr */"

    def gen_truthy_expr(self, expr: TpyExpr) -> str:
        """Generate a bool expression using Python-style truthiness semantics.

        For Optional value types, truthiness means "has value and contained value is truthy".
        """
        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            # Avoid double negation for `not isinstance(x, Proto)` on Optional protocol params.
            # The positive form already emits `!std::same_as<T_x, std::nullptr_t>`, so negating
            # that should yield `std::same_as<T_x, std::nullptr_t>` directly.
            operand = expr.operand
            if (isinstance(operand, TpyCall) and operand.isinstance_var is not None
                    and operand.isinstance_is_protocol and operand.isinstance_type is not None):
                var_name = operand.isinstance_var
                declared = self.ctx.current_func_params.get(var_name)
                if declared is not None:
                    infos = self.protocols.get_all_protocol_params([(var_name, declared)])
                    if infos and infos[0].has_none and len(infos[0].protocols) == 1:
                        return f"std::same_as<T_{var_name}, std::nullptr_t>"
            operand_truthy = self.gen_truthy_expr(operand)
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
        if self.ctx.is_indirect_name(expr):
            record = self.ctx.analyzer.registry.get_record_for_type(expr_type)
            if record and (record.get_method_overloads("__bool__")
                           or record.get_method_overloads("__len__")):
                rendered = f"(*{rendered})"
        return self._truthy_for_rendered(rendered, expr_type)

    def _truthy_for_rendered(self, rendered: str, var_type: TpyType) -> str:
        """Generate truthiness test for an already-rendered, already-dereferenced
        C++ expression. For pointer-locals, dereference before calling."""
        if isinstance(var_type, IntEnumType):
            cpp_underlying = var_type.underlying_type.to_cpp()
            return f"(static_cast<{cpp_underlying}>({rendered}) != 0)"
        if isinstance(var_type, EnumType):
            return "true"
        if isinstance(var_type, OptionalType) and not var_type.uses_pointer_repr():
            return f"::tpy::is_truthy({rendered})"
        if is_any_str_type(var_type):
            return f"(!{rendered}.empty())"
        record = self.ctx.analyzer.registry.get_record_for_type(var_type)
        if record:
            if record.get_method_overloads("__bool__"):
                return f"::tpy::__bool__({rendered})"
            if record.get_method_overloads("__len__"):
                return f"(::tpy::__len__({rendered}) != 0)"
            # User records without __bool__/__len__ are always truthy (Python default).
            # Builtin types (int, float, etc.) have implicit C++ bool conversion.
            if isinstance(var_type, NamedType) and var_type.is_user_record:
                return "true"
        # Implicit bool conversion (int, float, ptr, etc.)
        return rendered

    def _is_str_view_at_runtime(self, expr: TpyExpr) -> bool:
        """True if this expression produces std::string_view at C++ runtime.

        Cases that produce string_view despite having StrType as their sema type:
        - str params: C++ signature uses string_view (via to_cpp_param)
        - str literals: materialized as std::string_view temps in _gen_logical_value;
          treated as string_view here so recursive chain detection works correctly.
          Callers in _gen_if_expr must guard separately: literals are const char*
          there and need no explicit wrapping (C++ handles string?const-char* natively).
        - StrViewType locals: explicitly typed as string_view
        Nested and/or/ternary chains are handled recursively.
        """
        if isinstance(expr, TpyName):
            return (isinstance(self.ctx.current_func_params.get(expr.name), StrType)
                    or isinstance(self.types.get_resolved_type(expr), StrViewType))
        if isinstance(expr, TpyStrLiteral):
            return True
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            return (self._is_str_view_at_runtime(expr.left)
                    and self._is_str_view_at_runtime(expr.right))
        if isinstance(expr, TpyIfExpr):
            return (self._is_str_view_at_runtime(expr.then_expr)
                    and self._is_str_view_at_runtime(expr.else_expr))
        return isinstance(self.types.get_resolved_type(expr), StrViewType)

    def _gen_logical_value(self, expr: TpyBinOp, result_type: TpyType) -> str:
        """Generate and/or with Python operand semantics (returns operand, not bool).

        For variable operands (TpyName), uses the variable directly in both the
        truthiness test and the ternary branch -- no temp needed.

        For complex expressions (function calls, constructors), materializes
        into an auto&& temp so both ternary branches are lvalue names. This
        ensures the ternary is an lvalue and can bind to a reference, matching
        Python's reference semantics for non-value types.
        """
        lhs_type = self.types.get_resolved_type(expr.left)
        use_lhs_temp = not isinstance(expr.left, TpyName)

        if use_lhs_temp:
            left = self.gen_expr_deref(expr.left)
            # Bare braced-init-lists can't be used with auto&& or as ternary
            # branches -- C++ can't deduce the container type from {1,2} alone.
            # Prefix with the explicit type, same as the list-concat handling.
            if isinstance(lhs_type, ListType) and isinstance(expr.left, TpyArrayLiteral):
                left = f"{self.types.type_to_cpp(lhs_type)}{left}"
            # String literals are const char[N] -- auto&& keeps that type and
            # .empty() would fail. Use std::string_view to get a proper str type.
            lhs_cpp = "std::string_view" if (
                is_any_str_type(lhs_type) and isinstance(expr.left, TpyStrLiteral)
            ) else "auto&&"
            lhs_ref = self.ctx.temps.create_typed(lhs_cpp, left)
        else:
            lhs_ref = self.gen_expr_deref(expr.left)

        truthy = self._truthy_for_rendered(lhs_ref, lhs_type)
        # Propagate isinstance narrowing to RHS
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

        # Materialize RHS rvalues into temps so both ternary branches are
        # lvalues, avoiding dangling references for non-value types.
        use_rhs_temp = not isinstance(expr.right, TpyName)
        if use_rhs_temp:
            rhs_type = self.types.get_resolved_type(expr.right)
            if isinstance(rhs_type, ListType) and isinstance(expr.right, TpyArrayLiteral):
                right = f"{self.types.type_to_cpp(rhs_type)}{right}"
            rhs_cpp = "std::string_view" if (
                is_any_str_type(rhs_type) and isinstance(expr.right, TpyStrLiteral)
            ) else "auto&&"
            rhs_ref = self.ctx.temps.create_typed(rhs_cpp, right)
        else:
            rhs_ref = right

        lhs_branch = lhs_ref
        rhs_branch = rhs_ref
        # Only add explicit conversion when the two operands have different
        # C++ types from each other (e.g. one is string_view, other is string).
        # When they match, the ternary naturally produces their type and the
        # normal var decl handles any further conversion.
        # Determine effective C++ type of each arm for mismatch detection.
        # Str params and str literals both produce string_view in this context
        # (_is_str_view_at_runtime returns True for both).
        lhs_cpp_cmp = ("std::string_view" if is_any_str_type(lhs_type)
                       and self._is_str_view_at_runtime(expr.left)
                       else self.types.type_to_cpp(lhs_type))
        rhs_type_cmp = self.types.get_resolved_type(expr.right)
        rhs_cpp_cmp = ("std::string_view" if is_any_str_type(rhs_type_cmp)
                       and self._is_str_view_at_runtime(expr.right)
                       else self.types.type_to_cpp(rhs_type_cmp))
        if lhs_cpp_cmp != rhs_cpp_cmp:
            cpp_result = self.types.type_to_cpp(result_type)
            if cpp_result != lhs_cpp_cmp:
                lhs_branch = f"{cpp_result}({lhs_branch})"
            if cpp_result != rhs_cpp_cmp:
                rhs_branch = f"{cpp_result}({rhs_branch})"
        if expr.op == "||":
            return f"({truthy} ? {lhs_branch} : {rhs_branch})"
        else:
            return f"({truthy} ? {rhs_branch} : {lhs_branch})"

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
            negate = expr.op == "not in"
            if expr.resolved_contains:
                if expr.resolved_contains.cpp_template:
                    # Builtin __contains__ with template (dict, set, dict_keys)
                    find_expr = f"({expand_cpp_template(expr.resolved_contains.cpp_template, right, left)})"
                else:
                    # User-defined __contains__ method
                    find_expr = f"({right}.__contains__({left}))"
                return f"(!{find_expr})" if negate else find_expr
            elif is_any_str_type(self.types.get_resolved_type(expr.right)):
                # String contains: use .find(). Wrap string literals in
                # std::string_view since C string literals lack .find().
                rhs = f"std::string_view({right})" if isinstance(expr.right, TpyStrLiteral) else right
                op = "==" if negate else "!="
                return f"({rhs}.find({left}) {op} std::string::npos)"
            else:
                # Collection: use std::find
                op = "==" if negate else "!="
                return f"(std::find({right}.begin(), {right}.end(), {left}) {op} {right}.end())"

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
                # Static protocol params (single optional or union with None)
                # always use pointer comparison
                opt_type = left_type if opt_expr is expr.left else right_type
                if self.protocols.is_static_protocol_param(opt_type):
                    val = self.gen_expr(opt_expr)
                    cpp_op = "==" if expr.op == "is" else "!="
                    return f"({val} {cpp_op} nullptr)"
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
            # Nullable protocol union (non-OptionalType): pointer comparison
            for side_type, side_expr, other in [
                (left_type, expr.left, expr.right),
                (right_type, expr.right, expr.left),
            ]:
                if not isinstance(other, TpyNoneLiteral):
                    continue
                if not self.protocols.is_static_protocol_param(side_type):
                    continue
                infos = self.protocols.get_all_protocol_params([("_", side_type)])
                if infos and infos[0].has_none:
                    val = self.gen_expr(side_expr)
                    cpp_op = "==" if expr.op == "is" else "!="
                    return f"({val} {cpp_op} nullptr)"
            # Union types with NoneType member: std::holds_alternative<std::monostate>
            union_expr = None
            if isinstance(left_type, UnionType) and isinstance(expr.right, TpyNoneLiteral):
                union_expr = expr.left
            elif isinstance(right_type, UnionType) and isinstance(expr.left, TpyNoneLiteral):
                union_expr = expr.right
            if union_expr is not None:
                # In @overload context, the param is concrete (not a variant)
                if (isinstance(union_expr, TpyName)
                        and union_expr.name in self.ctx.overload_param_types):
                    concrete = self.ctx.overload_param_types[union_expr.name]
                    is_none = isinstance(concrete, NoneType)
                    if expr.op == "is":
                        return "true" if is_none else "false"
                    else:
                        return "false" if is_none else "true"
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

        # Logical operators
        if expr.op in ("&&", "||"):
            result_type = self.types.get_resolved_type(expr)
            if not isinstance(result_type, BoolType):
                # Value-context: Python operand semantics via temp + ternary.
                # `x or y` -> truthy(x) ? x : y
                # `x and y` -> truthy(x) ? y : x
                return self._gen_logical_value(expr, result_type)
            # Bool result: emit C++ &&/|| directly.
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
            left_target, right_target = self._comparison_targets(expr)
            left = self.gen_expr_deref(expr.left, left_target)
            right = self.gen_expr_deref(expr.right, right_target)

            # BigInt has no implicit conversion to/from double in C++, so
            # mixed BigInt/float comparisons need an explicit cast (mirroring
            # Python's int-to-float promotion for comparisons).
            left_cmp = left_target if left_target is not None else left_type
            right_cmp = right_target if right_target is not None else right_type
            if isinstance(left_cmp, BigIntType) and isinstance(right_cmp, (FloatType, Float32Type)):
                left = f"static_cast<{right_cmp.to_cpp()}>({left})"
            elif isinstance(right_cmp, BigIntType) and isinstance(left_cmp, (FloatType, Float32Type)):
                right = f"static_cast<{left_cmp.to_cpp()}>({right})"

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
                cpp_type = self.types.type_to_cpp(receiver_type)
                if isinstance(expr.left, TpyArrayLiteral):
                    left = f"{cpp_type}{left}"
                if isinstance(expr.right, TpyArrayLiteral):
                    right = f"{cpp_type}{right}"
            # Generate binop using helper (handles wrappers and is_reverse)
            result = self._gen_binop_from_result(binop_result, left, right)
            # Divisor proven non-zero: use unchecked variants
            if expr.divisor_non_zero:
                result = result.replace("div_check", "div_floor").replace("mod_check", "mod_floor")
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

    def _gen_comparison_pair(self, pair: TpyBinOp,
                             left_str: str, right_str: str) -> str:
        """Render a single comparison from pre-generated operand strings.

        Applies the same post-generation casts as _gen_binop (BigInt, IntEnum).
        """
        left_type = self.types.get_resolved_type(pair.left)
        right_type = self.types.get_resolved_type(pair.right)

        # BigInt/float mixed cast
        left_cmp = left_type
        right_cmp = right_type
        if isinstance(left_type, OptionalType):
            left_cmp = left_type.inner
        if isinstance(right_type, OptionalType):
            right_cmp = right_type.inner
        if isinstance(left_cmp, BigIntType) and isinstance(right_cmp, (FloatType, Float32Type)):
            left_str = f"static_cast<{right_cmp.to_cpp()}>({left_str})"
        elif isinstance(right_cmp, BigIntType) and isinstance(left_cmp, (FloatType, Float32Type)):
            right_str = f"static_cast<{left_cmp.to_cpp()}>({right_str})"

        # IntEnum coercion
        if pair.int_enum_coercion:
            underlying_cpp = self.types.type_to_cpp(pair.int_enum_coercion.underlying_type)
            if isinstance(left_type, IntEnumType):
                left_str = f"static_cast<{underlying_cpp}>({left_str})"
            if isinstance(right_type, IntEnumType):
                right_str = f"static_cast<{underlying_cpp}>({right_str})"

        return f"({left_str} {pair.op} {right_str})"

    @staticmethod
    def _is_simple_expr(expr: TpyExpr) -> bool:
        """Check if an expression is side-effect-free (safe to duplicate)."""
        if isinstance(expr, (
            TpyName, TpyIntLiteral, TpyFloatLiteral,
            TpyStrLiteral, TpyBoolLiteral, TpyNoneLiteral,
        )):
            return True
        if isinstance(expr, TpyFieldAccess):
            return ExpressionGenerator._is_simple_expr(expr.obj)
        return False

    def _gen_chained_compare(self, expr: TpyChainedCompare) -> str:
        """Generate chained comparison with hybrid strategy.

        Simple intermediates (names, literals): inline && chain.
        Complex intermediates (calls, subscripts): lambda IIFE with temp vars.
        """
        assert expr.pairs is not None
        intermediates = expr.comparators[:-1]
        if all(self._is_simple_expr(e) for e in intermediates):
            return self._gen_chained_compare_inline(expr)
        return self._gen_chained_compare_lambda(expr)

    def _gen_chained_compare_inline(self, expr: TpyChainedCompare) -> str:
        """Simple path: all intermediates are pure, desugar to && chain."""
        assert expr.pairs is not None
        parts = [self._gen_binop(pair, None) for pair in expr.pairs]
        result = parts[0]
        for part in parts[1:]:
            result = f"({result} && {part})"
        return result

    def _comparison_targets(self, pair: TpyBinOp) -> tuple[TpyType | None, TpyType | None]:
        """Determine target types for a comparison pair's operands.

        Handles Optional unwrapping and Char literal coercion,
        matching the logic in _gen_binop's comparison branch.
        """
        left_type = self.types.get_resolved_type(pair.left)
        right_type = self.types.get_resolved_type(pair.right)
        left_target: TpyType | None = None
        right_target: TpyType | None = None

        if pair.optional_safe_eq:
            left_analyzed = self.ctx.get_expr_type(pair.left)
            right_analyzed = self.ctx.get_expr_type(pair.right)
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
            if isinstance(left_type, OptionalType) and not left_type.uses_pointer_repr():
                left_target = left_type.inner
            if isinstance(right_type, OptionalType) and not right_type.uses_pointer_repr():
                right_target = right_type.inner

        # Char literal coercion
        if left_target is None and isinstance(right_type, CharType):
            if not (pair.optional_safe_eq and isinstance(left_type, OptionalType)):
                left_target = CHAR
        if right_target is None and isinstance(left_type, CharType):
            if not (pair.optional_safe_eq and isinstance(right_type, OptionalType)):
                right_target = CHAR

        return left_target, right_target

    def _gen_chained_compare_lambda(self, expr: TpyChainedCompare) -> str:
        """Complex path: lambda IIFE with temp vars for single evaluation.

        Interleaves operand evaluation with comparisons for short-circuit:
        operands after a failed comparison are never evaluated.
        """
        assert expr.pairs is not None
        all_operands = [expr.left] + expr.comparators
        lines: list[str] = []
        temps: list[str] = []
        for i, pair in enumerate(expr.pairs):
            left_target, right_target = self._comparison_targets(pair)
            # Generate left operand (or reuse previous right)
            if i == 0:
                tmp_l = f"_cmp{i}"
                temps.append(tmp_l)
                lines.append(f"auto&& {tmp_l} = {self.gen_expr_deref(all_operands[i], left_target)};")
            # Generate right operand
            tmp_r = f"_cmp{i + 1}"
            temps.append(tmp_r)
            lines.append(f"auto&& {tmp_r} = {self.gen_expr_deref(all_operands[i + 1], right_target)};")
            # Generate comparison with short-circuit
            cmp_str = self._gen_comparison_pair(pair, temps[i], tmp_r)
            if i < len(expr.pairs) - 1:
                lines.append(f"if (!{cmp_str}) return false;")
            else:
                lines.append(f"return {cmp_str};")
        body = " ".join(lines)
        return f"[&]() -> bool {{ {body} }}()"

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
            # In @overload context, param is already concrete -- no extraction needed
            if var_name in self.ctx.overload_param_types:
                continue
            cpp_type = self.types.type_to_cpp(narrowed_type)
            var_ref = var_name
            if var_name in self.ctx.narrowed_vars:
                var_ref = self.ctx.narrowed_vars[var_name]
            elif self.ctx.is_indirect_name(TpyName(var_name)):
                var_ref = f"(*{var_name})"
            # Pointer-variant unions: *std::get<T*>(var) or *std::get<const T*>(var)
            if var_name in self.ctx.ptr_variant_locals:
                const_pfx = "const " if var_name in self.ctx.const_indirect_locals else ""
                result[var_name] = f"(*std::get<{const_pfx}{cpp_type}*>({var_ref}))"
            else:
                result[var_name] = f"std::get<{cpp_type}>({var_ref})"
        return result

    def _extract_isinstance_facts(
        self, expr: TpyExpr, true_branch: bool, facts: dict[str, TpyType],
    ) -> None:
        """Walk expression tree to collect isinstance type facts."""
        if isinstance(expr, TpyCall) and expr.isinstance_var and expr.isinstance_type:
            # Protocol isinstance uses if constexpr -- no extraction needed
            if expr.isinstance_is_protocol:
                return
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
                return f"::tpy::BigInt({v})"
            if -2**63 <= v <= 2**63 - 1:
                return f"::tpy::BigInt(static_cast<int64_t>({v}LL))"
            return f'::tpy::BigInt::from_str("{v}")'
        return str(v)

    def _gen_call(self, expr: TpyCall) -> str:
        """Generate function call code."""
        # isinstance(x, Protocol) -> Concept<T_x>  (compile-time)
        if expr.isinstance_var is not None and expr.isinstance_is_protocol and expr.isinstance_type is not None:
            var_name = expr.isinstance_var
            # For Optional[Protocol] params (single protocol + None), use
            # !same_as<nullptr_t> guard instead of concept check because some
            # concepts (e.g. Sized) accidentally match nullptr_t via char* conversion.
            # Protocol unions with None still need concept checks to differentiate members.
            declared = self.ctx.current_func_params.get(var_name)
            if declared is not None:
                infos = self.protocols.get_all_protocol_params([(var_name, declared)])
                if infos and infos[0].has_none and len(infos[0].protocols) == 1:
                    return f"!std::same_as<T_{var_name}, std::nullptr_t>"
            return self.protocols._concept_constraint(expr.isinstance_var, expr.isinstance_type)
        # isinstance(x, T) -> std::holds_alternative<CppT>(x)
        if expr.isinstance_var is not None and expr.isinstance_type is not None:
            # In @overload context, param type is concrete -- resolve statically
            concrete = self.ctx.overload_param_types.get(expr.isinstance_var)
            if concrete is not None:
                check_type = expr.isinstance_type
                if concrete == check_type:
                    return "true"
                if isinstance(check_type, UnionType) and concrete in check_type.members:
                    return "true"
                return "false"
            cpp_type = self.types.type_to_cpp(expr.isinstance_type)
            var_name = expr.isinstance_var
            if var_name in self.ctx.narrowed_vars:
                var_name = self.ctx.narrowed_vars[var_name]
            name_node = TpyName(var_name)
            var_ref = self.gen_expr_deref(name_node) if self.ctx.is_indirect_name(name_node) else var_name
            # Pointer-variant unions: holds_alternative<T*> or <const T*>
            orig_var = expr.isinstance_var
            if orig_var in self.ctx.ptr_variant_locals:
                const_pfx = "const " if orig_var in self.ctx.const_indirect_locals else ""
                return f"std::holds_alternative<{const_pfx}{cpp_type}*>({var_ref})"
            return f"std::holds_alternative<{cpp_type}>({var_ref})"
        # Enum value lookup: Color(0) -> ::tpy::EnumUtil<Color>::from_value(0)
        if expr.enum_from_value is not None:
            enum_type = expr.enum_from_value
            cpp_type = enum_type.to_cpp()
            underlying_cpp = enum_type.underlying_type.to_cpp()
            arg = self.gen_expr(expr.args[0])
            # BigInt needs checked conversion to the underlying type
            arg_type = self.types.get_resolved_type(expr.args[0])
            if isinstance(arg_type, BigIntType):
                arg = f"({arg}).to_fixed_check<{underlying_cpp}>()"
            return f"::tpy::EnumUtil<{cpp_type}>::from_value({arg})"
        # Enum try_parse: try_parse(Color, "Red") -> ::tpy::EnumUtil<Color>::try_parse("Red")
        if expr.enum_try_parse is not None:
            enum_type = expr.enum_try_parse
            cpp_type = enum_type.to_cpp()
            arg = self.gen_expr(expr.args[1])
            return f"::tpy::EnumUtil<{cpp_type}>::try_parse({arg})"
        # Check if it's a builtin type constructor (e.g., int from builtins, Int32 from tpy)
        for module_name in ["builtins", "tpy"]:
            qname = f"{module_name}.{expr.func}"
            if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                if record_info.constructors and not record_info.type_params:
                    return self.builtins.gen_builtin_constructor(expr, record_info)
        # print() maps to std::printf
        if expr.func == "print":
            return self.builtins.gen_print(expr.args, expr.kwargs)
        # Check for imported function (builtins or from X import Y -> Y())
        # Builtins (len, pow, etc.) are registered in imported_names by the analyzer.
        if expr.func in self.ctx.analyzer.imported_names:
            is_shadowed = (expr.func in self.ctx.declared_vars or
                           expr.func in self.ctx.global_names or
                           self.ctx.analyzer.registry.get_function(expr.func) is not None or
                           self.ctx.analyzer.registry.get_record(expr.func) is not None)
            if not is_shadowed:
                module_name, func_name = self.ctx.analyzer.imported_names[expr.func]
                # copy(x) from tpy - produce an explicit copy (rvalue) of x
                if module_name == "tpy" and func_name == "copy":
                    return self._gen_copy_expr(expr.args[0])
                # Check for module function
                module_info = self.ctx.analyzer.registry.get_module(module_name)
                if module_info and func_name in module_info.functions:
                    return self.builtins.gen_template_or_native_call(expr, module_info.functions[func_name])
                # Check for type constructor (e.g., Int32 from tpy, int from builtins)
                qname = f"{module_name}.{func_name}"
                if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                    if record_info.constructors and not record_info.type_params:
                        return self.builtins.gen_builtin_constructor(expr, record_info)
        # Check if this is a function call that needs argument conversion
        func_infos = self.ctx.analyzer.registry.get_function(expr.func)
        if func_infos:
            # For overload groups, use the sema-resolved stub (not [0] which
            # is arbitrary). For single functions, [0] is the only entry.
            # Note: don't use resolved_function_info for generic functions
            # because it has substituted types (breaks is_generic() detection).
            if len(func_infos) > 1 and expr.resolved_function_info:
                func_info = expr.resolved_function_info
            else:
                func_info = func_infos[0]
            # Build type substitution for generic functions
            type_subst = {}
            if func_info.is_generic() and expr.inferred_type_args:
                type_subst = dict(zip(func_info.type_params, expr.inferred_type_args))

            # @cpp_template functions: expand the C++ expression template directly
            if func_info.cpp_template:
                return self.builtins.gen_template_or_native_call(expr, [func_info])

            gen_args = []
            for arg, (pname, ptype) in zip(expr.args, func_info.params):
                # Resolve TypeParamRef for generic functions
                resolved_ptype = self.types.substitute_type_params(ptype, type_subst) if type_subst else ptype

                # T() default-construction: emit ConcreteType{}
                if isinstance(arg, TpyTypeParamConstruct):
                    gen_args.append(f"{self.types.type_to_cpp(resolved_ptype)}{{}}")
                    continue

                # @dynamic protocol params: wrap concrete args in temp adapter
                dynamic_arg = self._gen_dynamic_protocol_arg(arg, resolved_ptype)
                if dynamic_arg is not None:
                    gen_args.append(dynamic_arg)
                    continue

                # Covariant generic params: Box[Child] -> Box[Parent] via temp
                covariant_arg = self._gen_covariant_arg(arg, resolved_ptype)
                if covariant_arg is not None:
                    gen_args.append(covariant_arg)
                    continue

                # Static protocol params (single, optional, union)
                proto_arg = self._gen_protocol_arg(arg, resolved_ptype)
                if proto_arg is not None:
                    gen_args.append(proto_arg)
                    continue

                opt_arg = self._gen_optional_ptr_arg(arg, resolved_ptype)
                if opt_arg is not None:
                    gen_args.append(opt_arg)
                # Temporaries passed to mutable reference params need a temp variable
                # because C++ can't bind rvalue to non-const lvalue reference
                # TypeParamRef generates param_val_or_ref_t<T> which is T& for object types
                elif (resolved_ptype.is_ref_param() or isinstance(ptype, TypeParamRef)) and self.ctx.is_temporary_expr(arg):
                    init_expr = self.gen_expr(arg, resolved_ptype)
                    temp_name = self.ctx.temps.create(resolved_ptype, init_expr)
                    gen_args.append(temp_name)
                # Union params: wrap concrete member type in variant
                elif (union_arg := self._gen_union_arg(arg, resolved_ptype,
                                                        is_readonly_target=func_info.is_readonly)) is not None:
                    gen_args.append(union_arg)
                else:
                    gen_args.append(self.gen_call_arg(arg, resolved_ptype))

            # Determine function name
            func_cpp_name = escape_cpp_name(expr.func)
            if func_info.is_native_import or func_info.is_extern_c:
                # @native/@native_c/@extern_c: use the C/C++ symbol name directly.
                # For @native_c, the calling module's header has a local re-declaration
                # so no namespace qualification is needed (works for same-module,
                # cross-module, and package re-export cases).
                func_cpp_name = qualify_native_name(func_info.native_name or func_info.name)
            elif expr.func in self.ctx.user_imported_functions:
                source_module, original_name = self.ctx.user_imported_functions[expr.func]
                func_cpp_name = qualified_cpp_name(source_module, original_name)

            # For generic TPy functions, emit explicit type args to avoid C++ deduction
            # issues with ::tpy::param_val_or_ref_t<T> parameters.  Skip for @native
            # functions -- their C++ signatures use natural parameter types so
            # template argument deduction works correctly.
            if func_info.is_generic() and expr.inferred_type_args and not func_info.is_native_import:
                type_args_str = ", ".join(self.types.type_to_cpp(t) for t in expr.inferred_type_args)
                return f"{func_cpp_name}<{type_args_str}>({', '.join(gen_args)})"
            return f"{func_cpp_name}({', '.join(gen_args)})"
        # Generic type instantiation (e.g., Container[T, N]())
        if expr.call_type is not None:
            # Pointer null constructors: Ptr[T]() / Ptr[readonly[T]]() -> typed nullptr
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
                if isinstance(a, TpyTypeParamConstruct) and ptype:
                    gen_args.append(f"{self.types.type_to_cpp(ptype)}{{}}")
                    continue
                # @dynamic protocol params
                if ptype is not None:
                    dynamic_arg = self._gen_dynamic_protocol_arg(a, ptype)
                    if dynamic_arg is not None:
                        gen_args.append(dynamic_arg)
                        continue
                    covariant_arg = self._gen_covariant_arg(a, ptype)
                    if covariant_arg is not None:
                        gen_args.append(covariant_arg)
                        continue
                proto_arg = self._gen_protocol_arg(a, ptype)
                if proto_arg is not None:
                    gen_args.append(proto_arg)
                    continue
                opt_arg = self._gen_optional_ptr_arg(a, ptype)
                if opt_arg is not None:
                    gen_args.append(opt_arg)
                    continue
                # None literals need param type for nullptr vs std::nullopt;
                # array literals need call_type for nested brace generation.
                if isinstance(a, TpyNoneLiteral) and ptype:
                    gen_args.append(self.gen_call_arg(a, ptype, target_type=ptype))
                elif isinstance(a, TpyArrayLiteral):
                    gen_args.append(self.gen_call_arg(a, ptype, target_type=expr.call_type))
                else:
                    gen_args.append(self.gen_call_arg(a, ptype))
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
                if isinstance(a, TpyTypeParamConstruct) and ptype:
                    gen_args.append(f"{self.types.type_to_cpp(ptype)}{{}}")
                    continue
                # @dynamic protocol params in constructor
                if ptype is not None:
                    dynamic_arg = self._gen_dynamic_protocol_arg(a, ptype)
                    if dynamic_arg is not None:
                        gen_args.append(dynamic_arg)
                        continue
                    covariant_arg = self._gen_covariant_arg(a, ptype)
                    if covariant_arg is not None:
                        gen_args.append(covariant_arg)
                        continue
                proto_arg = self._gen_protocol_arg(a, ptype)
                if proto_arg is not None:
                    gen_args.append(proto_arg)
                elif (opt_arg := self._gen_optional_ptr_arg(a, ptype)) is not None:
                    gen_args.append(opt_arg)
                elif (union_arg := self._gen_union_arg(a, ptype)) is not None:
                    gen_args.append(union_arg)
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
        # Skip upfront arg generation when a later path will regenerate args:
        # - cpp_template methods: handled by gen_method_from_function_info
        # - user-record methods with known method: handled by TypeParamRef temp path
        # Running the first-pass AND a later path creates duplicate TempState entries.
        _fi = expr.resolved_function_info
        _skip_first_pass = (_fi is not None
                            and (_fi.cpp_template is not None or _fi.native_function))
        if not _skip_first_pass:
            # self.method() returns early before the user-record TypeParamRef path,
            # so it needs the first-pass args with full protocol/covariant handling.
            _is_self_call = isinstance(expr.obj, TpyName) and expr.obj.name == "self"
            if not _is_self_call:
                _obj_type = self.types.get_resolved_type(expr.obj)
                if isinstance(_obj_type, NamedType) and _obj_type.is_user_record:
                    _ri = self.ctx.analyzer.registry.get_record_for_type(_obj_type)
                    if _ri and _ri.get_method(expr.method):
                        _skip_first_pass = True
        _is_native_stub = _fi is not None and bool(_fi.native_name or _fi.cpp_template)
        if not _skip_first_pass and _fi:
            params = expr.resolved_function_info.params
            gen_args = []
            for i, arg in enumerate(expr.args):
                ptype = params[i].type if i < len(params) else None
                if isinstance(arg, TpyTypeParamConstruct):
                    assert ptype is not None, f"No param type for TpyTypeParamConstruct at arg {i}"
                    gen_args.append(f"{self.types.type_to_cpp(ptype)}{{}}")
                else:
                    # @dynamic protocol params in method calls
                    if ptype is not None:
                        dynamic_arg = self._gen_dynamic_protocol_arg(arg, ptype)
                        if dynamic_arg is not None:
                            gen_args.append(dynamic_arg)
                            continue
                        covariant_arg = self._gen_covariant_arg(arg, ptype)
                        if covariant_arg is not None:
                            gen_args.append(covariant_arg)
                            continue
                    proto_arg = self._gen_protocol_arg(arg, ptype)
                    if proto_arg is not None:
                        gen_args.append(proto_arg)
                        continue
                    opt_arg = self._gen_optional_ptr_arg(arg, ptype)
                    if opt_arg is not None:
                        gen_args.append(opt_arg)
                        continue
                    union_arg = self._gen_union_arg(arg, ptype)
                    if union_arg is not None:
                        gen_args.append(union_arg)
                        continue
                    gen_args.append(self.gen_call_arg(arg, ptype,
                                                      inline_template=_is_native_stub))
            args = ", ".join(gen_args)
        else:
            args = ", ".join(self.gen_expr_deref(a) for a in expr.args)

        # Handle user module function calls: module.func() -> ::tpyapp::module::func()
        if expr.user_module_call is not None:
            fi = expr.resolved_function_info
            # @cpp_template: expand inline regardless of module origin
            if fi and fi.cpp_template:
                from ..parse import TpyCall
                temp_call = TpyCall(func=expr.method, args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                temp_call.resolved_function_info = fi
                temp_call.inferred_type_args = expr.inferred_type_args
                return self.builtins.gen_template_or_native_call(temp_call, [fi])
            if fi and (fi.is_native_import or fi.is_extern_c):
                func_name = fi.native_name or fi.name
                # Qualified native names (e.g. "::tpy::math::log_base") are absolute
                # C++ symbols -- prefix with :: and don't wrap in the user module namespace.
                if "::" in func_name:
                    return f"{qualify_native_name(func_name)}({args})"
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
                return f"::tpy::EnumUtil<{cpp_type}>::try_parse({arg})"
            # copy() from tpy -- produce an explicit copy (rvalue) of the argument
            if module_name == "tpy" and expr.method == "copy":
                return self._gen_copy_expr(expr.args[0])
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
                return self.builtins.gen_template_or_native_call(temp_call, module_info.functions[expr.method])
            # Check for type constructor (e.g., tpy.Int32)
            qname = f"{module_name}.{expr.method}"
            if record_info := self.ctx.analyzer.registry.get_builtin_record(qname):
                if record_info.constructors and not record_info.type_params:
                    from ..parse import TpyCall
                    temp_call = TpyCall(func=expr.method, args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                    return self.builtins.gen_builtin_constructor(temp_call, record_info)

        # Check for builtin method with native_function or cpp_template first
        # This must be checked before the self.method() shortcut because
        # inherited builtin methods need special codegen
        if expr.resolved_function_info:
            method_info = expr.resolved_function_info
            if method_info.native_function and method_info.native_name:
                receiver = self._gen_builtin_method_receiver(expr)
                gen_args = [self.builtins._gen_call_arg(arg, method_info.params[i].type if i < len(method_info.params) else None,
                                                        inline_template=True)
                            for i, arg in enumerate(expr.args)]
                return f"{qualify_native_name(method_info.native_name)}({', '.join([receiver] + gen_args)})"
            if method_info.cpp_template:
                # Static method on builtin type — no receiver, just args
                if expr.is_static_call:
                    gen_args = [self.builtins._gen_expr_deref(arg, ptype)
                                for arg, (_, ptype) in zip(expr.args, method_info.params)]
                    return method_info.cpp_template.format(*gen_args)
                receiver = self._gen_builtin_method_receiver(expr)
                return self.builtins.gen_method_from_function_info(receiver, expr.args, method_info)

        # Build explicit template args for generic method calls
        method_targs = ""
        if expr.inferred_type_args and not expr.user_module_call and not expr.is_static_call:
            method_targs = "<" + ", ".join(self.types.type_to_cpp(t) for t in expr.inferred_type_args) + ">"

        # Handle self.method() -> just method() (inside method, implicit this)
        if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
            return f"{expr.method}{method_targs}({args})"
        # Handle super().method() -> ParentClass::method(args)
        if expr.super_parent_type is not None:
            parent_cpp = expr.super_parent_type.to_cpp()
            # C++ requires 'template' keyword before dependent template names
            template_kw = "template " if method_targs else ""
            return f"{parent_cpp}::{template_kw}{expr.method}{method_targs}({args})"
        # Handle ClassName.staticmethod() -> ClassName::staticmethod()
        if expr.is_static_call and isinstance(expr.obj, TpyName):
            # For native records, use the C++ class and method names
            record_info = self.ctx.analyzer.registry.get_record(expr.obj.name)
            if record_info and record_info.is_native:
                cpp_class = record_info.native_name or expr.obj.name
                cpp_method = expr.resolved_function_info.native_name if expr.resolved_function_info and expr.resolved_function_info.native_name else expr.method
                return f"{cpp_class}::{cpp_method}({args})"
            class_name = expr.obj.name
            static_method_targs = ""
            if expr.inferred_type_args:
                # Split inferred type args into class-level and method-level
                n_class = len(record_info.type_params) if record_info and record_info.type_params else 0
                class_args = expr.inferred_type_args[:n_class]
                method_args = expr.inferred_type_args[n_class:]
                if class_args:
                    type_args_str = ", ".join(self.types.type_to_cpp(t) for t in class_args)
                    class_name = f"{class_name}<{type_args_str}>"
                if method_args:
                    static_method_targs = "<" + ", ".join(self.types.type_to_cpp(t) for t in method_args) + ">"
            return f"{class_name}::{expr.method}{static_method_targs}({args})"
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
                        return self.builtins.gen_template_or_native_call(temp_call, module_info.functions[expr.method])
        obj = self.gen_expr(expr.obj)
        obj, is_assign_narrowed = self._apply_assign_narrowing(expr.obj, obj)
        obj_type = self.types.get_resolved_type(expr.obj)

        # Consuming method: wrap receiver in std::move() for rvalue-qualified call.
        # For pointer-locals (T* internally), dereference before moving.
        is_consuming = (expr.resolved_function_info is not None
                        and expr.resolved_function_info.is_consuming)
        if is_consuming and isinstance(expr.obj, TpyName):
            if self.ctx.is_indirect_name(expr.obj) and not is_assign_narrowed:
                obj = f"std::move(*{obj})"
            else:
                obj = f"std::move({obj})"

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
                method_obj = self._maybe_unwrap_narrowed_optional(expr.obj, method_obj, needs_deref)
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
                    # Also include method-level type args (e.g. U -> Int32 for transform[U])
                    # so TypeParamRef params resolve to concrete types for temp decisions.
                    type_subst = {}
                    if record_info.type_params and obj_type.type_args:
                        for param_name, arg_type in zip(record_info.type_params, obj_type.type_args):
                            type_subst[param_name] = arg_type
                    if expr.inferred_type_args and method_info.type_params:
                        class_params = set(record_info.type_params) if record_info.type_params else set()
                        for tp, ta in zip(
                            [p for p in method_info.type_params if p not in class_params],
                            expr.inferred_type_args,
                        ):
                            type_subst[tp] = ta
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
                            proto_arg = self._gen_protocol_arg(arg, rptype)
                            if proto_arg is not None:
                                gen_args.append(proto_arg)
                            elif (opt_arg := self._gen_optional_ptr_arg(arg, rptype)) is not None:
                                gen_args.append(opt_arg)
                            elif (union_arg := self._gen_union_arg(arg, rptype,
                                                                    is_readonly_target=method_info.is_readonly)) is not None:
                                gen_args.append(union_arg)
                            else:
                                # None literals need target type to decide nullptr vs std::nullopt
                                arg_target = rptype if isinstance(arg, TpyNoneLiteral) else None
                                # @native stub methods: skip redundant copy-then-move
                                is_native_stub = bool(method_info.native_name or method_info.cpp_template)
                                gen_args.append(self.gen_call_arg(arg, rptype, target_type=arg_target,
                                                                  inline_template=is_native_stub))
                    args = ", ".join(gen_args)

        # Use -> for pointer-locals/globals (T*) and pointer-typed expressions
        # (OptionalType non-value expressions like function calls return T*)
        obj_type = self.ctx.get_expr_type(expr.obj)
        is_optional_ptr = isinstance(obj_type, OptionalType) and obj_type.uses_pointer_repr()
        # Narrowed std::optional<T>: sema sees T but C++ var is still std::optional<T>
        if not isinstance(obj_type, OptionalType):
            cpp_decl = self._get_cpp_declared_type(expr.obj)
            if isinstance(cpp_decl, OptionalType) and not cpp_decl.uses_pointer_repr():
                obj = f"(*{obj})"
        deref_chain = ".__deref__()" * expr.deref_depth
        # Optional with runtime null check -- must come before deref fast path
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"::tpy::deref_optional_check({obj}){deref_chain}.{expr.method}{method_targs}({args})"
            # For pointer-globals with wrapper storage, this yields raw `T*`.
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"::tpy::deref_check({ptr_expr}){deref_chain}.{expr.method}{method_targs}({args})"
        # User-defined Deref: emit .__deref__() calls before method call
        is_narrowed = (isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.narrowed_vars) or is_assign_narrowed
        if deref_chain and obj_type and not obj_type.is_pointer():
            is_indirect = self.ctx.is_indirect_name(expr.obj) and not is_narrowed
            if is_indirect or is_optional_ptr:
                return f"{obj}->{deref_chain[1:]}.{expr.method}{method_targs}({args})"
            return f"{obj}{deref_chain}.{expr.method}{method_targs}({args})"
        if obj_type and obj_type.is_pointer():
            if expr.ptr_non_null:
                return f"{obj}->{expr.method}{method_targs}({args})"
            return f"::tpy::deref_check({obj}).{expr.method}{method_targs}({args})"
        use_arrow = ((self.ctx.is_indirect_name(expr.obj) and not is_narrowed and not is_consuming)
                     or is_optional_ptr)
        accessor = "->" if use_arrow else "."
        # Use native method name if available (for @native/@native_c class methods)
        cpp_method = expr.resolved_function_info.native_name if expr.resolved_function_info and expr.resolved_function_info.native_name else expr.method
        return f"{obj}{accessor}{cpp_method}{method_targs}({args})"

    def _gen_builtin_method_receiver(self, expr: TpyMethodCall) -> str:
        """Generate the receiver expression for a builtin method call (cpp_template or native_function)."""
        if isinstance(expr.obj, TpyName) and expr.obj.name == "self":
            return "(*this)"
        obj = self.gen_expr(expr.obj)
        obj, an = self._apply_assign_narrowing(expr.obj, obj)
        is_ptr_deref = expr.deref_depth > 0 and self.types.get_resolved_type(expr.obj).is_pointer()
        needs_deref = (self.ctx.is_indirect_name(expr.obj) and not an) or is_ptr_deref
        receiver = f"(*{obj})" if needs_deref else obj
        return self._maybe_unwrap_narrowed_optional(expr.obj, receiver, needs_deref)

    def _apply_assign_narrowing(self, expr_obj: TpyExpr, obj_code: str) -> tuple[str, bool]:
        """Apply inline std::get wrapping for assignment-narrowed union vars.

        Returns (possibly wrapped code, was_narrowed).
        """
        if isinstance(expr_obj, TpyName) and expr_obj.name in self.ctx.assign_narrowed_types:
            # In @overload context, param is already concrete -- skip std::get
            if expr_obj.name in self.ctx.overload_param_types:
                return obj_code, False
            # Already isinstance-narrowed -- std::get extraction was done at block entry
            if expr_obj.name in self.ctx.narrowed_vars:
                return obj_code, False
            narrowed_type = self.ctx.assign_narrowed_types[expr_obj.name]
            cpp_type = self.types.type_to_cpp(narrowed_type)
            # Pointer-variant unions: *std::get<T*>(var) or *std::get<const T*>(var)
            if expr_obj.name in self.ctx.ptr_variant_locals:
                const_pfx = "const " if expr_obj.name in self.ctx.const_indirect_locals else ""
                return f"(*std::get<{const_pfx}{cpp_type}*>({obj_code}))", True
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
                        var_info = module_info.variables[expr.field]
                        # Non-value-type globals are T* pointers -- dereference for value access
                        if var_info.is_pointer:
                            return f"(*{var_info.cpp_expr})"
                        return var_info.cpp_expr

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
                return f"::tpy::EnumUtil<{cpp_type}>::name({obj})"
            elif expr.field == "value":
                return f"static_cast<{actual_obj_type.underlying_type.to_cpp()}>({obj})"

        # Narrowed vars (from isinstance std::get) are direct references, not pointers
        is_narrowed = (isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.narrowed_vars) or is_assign_narrowed
        is_indirect = self.ctx.is_indirect_name(expr.obj) and not is_narrowed
        is_optional_ptr = isinstance(obj_type, OptionalType) and obj_type.uses_pointer_repr()
        # Narrowed std::optional<T>: sema sees T but C++ var is still std::optional<T>
        if not isinstance(obj_type, OptionalType):
            cpp_decl = self._get_cpp_declared_type(expr.obj)
            if isinstance(cpp_decl, OptionalType) and not cpp_decl.uses_pointer_repr():
                obj = f"(*{obj})"
        deref_chain = ".__deref__()" * expr.deref_depth
        # Optional with runtime null check -- must come before deref fast path
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"::tpy::deref_optional_check({obj}){deref_chain}.{cpp_field}"
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"::tpy::deref_check({ptr_expr}){deref_chain}.{cpp_field}"
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
            return f"::tpy::deref_check({obj}).{cpp_field}"
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
        # For list literals with Optional/Union/Tuple element types, pass element target
        # so None generates std::nullopt, and tuple literals get expected types propagated.
        if elem_target is None and target_type:
            et = target_type.get_element_type()
            if isinstance(et, (OptionalType, UnionType, TupleType, StrType)):
                elem_target = et
        elements = []
        for e in expr.elements:
            # Use elem_target for generation (preserves old int-literal behavior, and for
            # tuple elements passes the full slot type into _gen_tuple_literal so STR hints
            # reach nested str slots).
            code = self.gen_expr_deref(e, elem_target)
            resolved = self.types.get_resolved_type(e, elem_target)
            elements.append(self._wrap_for_owned_slot(code, resolved, elem_target))
        literal = f"{{{', '.join(elements)}}}"
        # std::array of std::array needs an extra brace level
        if isinstance(elem_target, ArrayType):
            return f"{{{literal}}}"
        # Empty list needs explicit type to avoid ambiguity with T* assignment
        if not expr.elements and target_type and target_type.get_element_type() is not None:
            return f"{target_type.to_cpp()}{literal}"
        return literal

    def _gen_dict_literal(self, expr: TpyDictLiteral) -> str:
        """Generate dict literal code: {k: v, ...} -> ::tpy::ordered_map<K, V>({{k, v}, ...})"""
        dict_type = self.ctx.get_expr_type(expr)
        assert isinstance(dict_type, DictType)
        cpp_key = dict_type.key_type.to_cpp()
        cpp_val = dict_type.value_type.to_cpp()

        if not expr.keys:
            return f"::tpy::ordered_map<{cpp_key}, {cpp_val}>()"

        pairs = []
        for k, v in zip(expr.keys, expr.values):
            k_resolved = self.types.get_resolved_type(k, dict_type.key_type)
            k_cpp = self._wrap_for_owned_slot(self.gen_expr_deref(k, dict_type.key_type), k_resolved, dict_type.key_type)
            v_resolved = self.types.get_resolved_type(v, dict_type.value_type)
            v_cpp = self._wrap_for_owned_slot(self.gen_expr_deref(v, dict_type.value_type), v_resolved, dict_type.value_type)
            pairs.append(f"{{{k_cpp}, {v_cpp}}}")
        return f"::tpy::ordered_map<{cpp_key}, {cpp_val}>({{{', '.join(pairs)}}})"

    def _gen_set_literal(self, expr: TpySetLiteral) -> str:
        """Generate set literal code: {a, b, ...} -> ::tpy::ordered_set<T>({a, b, ...})"""
        set_type = self.ctx.get_expr_type(expr)
        assert isinstance(set_type, SetType)
        cpp_elem = set_type.element_type.to_cpp()

        if not expr.elements:
            return f"::tpy::ordered_set<{cpp_elem}>()"

        elems = []
        for e in expr.elements:
            e_resolved = self.types.get_resolved_type(e, set_type.element_type)
            elems.append(self._wrap_for_owned_slot(self.gen_expr_deref(e, set_type.element_type), e_resolved, set_type.element_type))
        return f"::tpy::ordered_set<{cpp_elem}>({{{', '.join(elems)}}})"

    def _gen_list_repeat(self, expr: TpyListRepeat, target_type: TpyType | None) -> str:
        """Generate list repeat code."""
        # [elements...] * N -> repeated sequence
        # Note: Empty list repetition [] * N is collapsed to [] in the parser

        count = self.gen_expr_deref(expr.count)
        count_type = self.ctx.analyzer.get_expr_type(expr.count)
        # BigInt count needs conversion (IntLiteralType is already plain int)
        if isinstance(count_type, BigIntType):
            count = f"{count}.to_fixed_check<int32_t>()"

        # Determine result type and element type.
        # For protocol targets and Span targets, use the resolved expr type:
        # protocols don't map to concrete C++ container types, and Span can't
        # be constructed from a range (needs contiguous memory from Array/list).
        use_resolved = (target_type is None
                        or is_protocol_type(target_type)
                        or isinstance(target_type, SpanType))
        if not use_resolved:
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
        repeat_elems = []
        for e in expr.elements:
            e_resolved = self.types.get_resolved_type(e, elem_type)
            repeat_elems.append(self._wrap_for_owned_slot(self.gen_expr_deref(e, elem_type or e_resolved), e_resolved, elem_type))
        elements = ", ".join(repeat_elems)
        cpp_elem_type = elem_type.to_cpp() if elem_type else "auto"
        range_expr = f"::tpy::repeat_range<{cpp_elem_type}>({count}, {{{elements}}})"

        # Lazy: resolved to ListRepeatType -- emit bare repeat_range (no materialization)
        if isinstance(result_type, ListRepeatType):
            return range_expr

        # Materialized: wrap in from_range to construct the target container
        cpp_type = result_type.to_cpp()
        return f"::tpy::from_range<{cpp_type}>({range_expr})"

    def _gen_list_comprehension(self, expr: TpyListComprehension,
                                target_type: TpyType | None = None) -> str:
        elem_type = self._resolve_int_literal(expr.result_elem_type)
        cpp_elem = self.types.type_to_cpp(elem_type)

        if isinstance(target_type, ArrayType):
            return self._gen_array_comprehension(expr, elem_type, cpp_elem, target_type.size)

        comp_names = self._enter_comp_scope(expr.generator)
        try:
            elem_resolved = self.types.get_resolved_type(expr.element_expr, elem_type)
            insert_code = self._wrap_for_owned_slot(self.gen_expr_deref(expr.element_expr, elem_type), elem_resolved, elem_type)
            return self._gen_comprehension_iife(
                expr.generator, f"std::vector<{cpp_elem}>",
                f"__result.push_back({insert_code})", skip_reserve=False)
        finally:
            self._exit_comp_scope(comp_names)

    def _gen_array_comprehension(self, expr: TpyListComprehension,
                                  elem_type: TpyType, cpp_elem: str,
                                  size: int) -> str:
        """Generate comprehension as IIFE producing std::array with indexed assignment."""
        gen = expr.generator
        comp_names = self._enter_comp_scope(gen)
        try:
            elem_resolved = self.types.get_resolved_type(expr.element_expr, elem_type)
            insert_code = self._wrap_for_owned_slot(self.gen_expr_deref(expr.element_expr, elem_type), elem_resolved, elem_type)

            stmt_ind = INDENT * self.ctx.indent_level
            ind1 = stmt_ind + INDENT
            ind2 = ind1 + INDENT

            buf = io.StringIO()
            buf.write(f"[&]() {{\n")
            buf.write(f"{ind1}std::array<{cpp_elem}, {size}> __result;\n")

            is_range = isinstance(gen.iterable, TpyCall) and gen.iterable.func == "range"
            nargs = len(gen.iterable.args) if is_range else 0
            cpp_var = escape_cpp_name(gen.var)

            if is_range and nargs <= 2:
                sema_elem = self.types.get_resolved_type(gen.iterable).get_iteration_element_type()
                if sema_elem is not None and isinstance(sema_elem, IntLiteralType):
                    sema_elem = self.ctx.analyzer.ctx.default_int_type
                if sema_elem is None:
                    sema_elem = self.ctx.analyzer.ctx.default_int_type
                cpp_iter_type = sema_elem.to_cpp()

                if nargs == 1:
                    buf.write(f"{ind1}for ({cpp_iter_type} {cpp_var} = 0; {cpp_var} < {size}; ++{cpp_var}) {{\n")
                else:
                    start_code = self.gen_expr_deref(gen.iterable.args[0])
                    buf.write(f"{ind1}const {cpp_iter_type} __start_0 = {start_code};\n")
                    buf.write(f"{ind1}for ({cpp_iter_type} {cpp_var} = __start_0, __idx_0 = 0;"
                              f" __idx_0 < {size}; ++{cpp_var}, ++__idx_0) {{\n")
                idx_expr = cpp_var if nargs == 1 else "__idx_0"
                buf.write(f"{ind2}__result[{idx_expr}] = {insert_code};\n")
            else:
                # Array/container source -- begin/end loop with index counter
                n = self.ctx.iter_counter
                self.ctx.iter_counter += 1
                iterable_code = self.gen_expr_deref(gen.iterable)
                is_lvalue = self._comp_is_lvalue(gen.iterable)
                obj_binding = "auto&" if is_lvalue else "auto"

                sema_elem = self.types.get_resolved_type(gen.iterable).get_iteration_element_type()
                if sema_elem is not None and isinstance(sema_elem, IntLiteralType):
                    sema_elem = self.ctx.analyzer.ctx.default_int_type
                if sema_elem is None:
                    sema_elem = self.ctx.analyzer.ctx.default_int_type

                buf.write(f"{ind1}{obj_binding} __obj_{n} = {iterable_code};\n")
                buf.write(f"{ind1}auto __beg_{n} = __obj_{n}.begin();\n")
                buf.write(f"{ind1}auto __end_{n} = __obj_{n}.end();\n")
                buf.write(f"{ind1}for (size_t __idx_{n} = 0; __beg_{n} != __end_{n}; ++__beg_{n}, ++__idx_{n}) {{\n")

                if gen.unpack_vars is not None:
                    self._gen_comp_tuple_unpack(buf, gen, sema_elem, ind2, n)
                elif sema_elem.is_value_type():
                    cpp_iter_elem = sema_elem.to_cpp()
                    buf.write(f"{ind2}{cpp_iter_elem} {cpp_var} = *__beg_{n};\n")
                else:
                    buf.write(f"{ind2}auto&& {cpp_var} = *__beg_{n};\n")

                buf.write(f"{ind2}__result[__idx_{n}] = {insert_code};\n")

            buf.write(f"{ind1}}}\n")
            buf.write(f"{ind1}return __result;\n")
            buf.write(f"{stmt_ind}}}()")

            return buf.getvalue()
        finally:
            self._exit_comp_scope(comp_names)

    def _gen_dict_comprehension(self, expr: TpyDictComprehension) -> str:
        key_type = self._resolve_int_literal(expr.result_key_type)
        value_type = self._resolve_int_literal(expr.result_value_type)
        cpp_key = self.types.type_to_cpp(key_type)
        cpp_val = self.types.type_to_cpp(value_type)
        comp_names = self._enter_comp_scope(expr.generator)
        try:
            key_resolved = self.types.get_resolved_type(expr.key_expr, key_type)
            key_code = self._wrap_for_owned_slot(self.gen_expr_deref(expr.key_expr, key_type), key_resolved, key_type)
            value_resolved = self.types.get_resolved_type(expr.value_expr, value_type)
            value_code = self._wrap_for_owned_slot(self.gen_expr_deref(expr.value_expr, value_type), value_resolved, value_type)
            return self._gen_comprehension_iife(
                expr.generator, f"::tpy::ordered_map<{cpp_key}, {cpp_val}>",
                f"__result.insert_or_assign({key_code}, {value_code})", skip_reserve=True)
        finally:
            self._exit_comp_scope(comp_names)

    def _gen_set_comprehension(self, expr: TpySetComprehension) -> str:
        elem_type = self._resolve_int_literal(expr.result_elem_type)
        cpp_elem = self.types.type_to_cpp(elem_type)
        comp_names = self._enter_comp_scope(expr.generator)
        try:
            elem_resolved = self.types.get_resolved_type(expr.element_expr, elem_type)
            insert_code = self._wrap_for_owned_slot(self.gen_expr_deref(expr.element_expr, elem_type), elem_resolved, elem_type)
            return self._gen_comprehension_iife(
                expr.generator, f"::tpy::ordered_set<{cpp_elem}>",
                f"__result.insert({insert_code})", skip_reserve=True)
        finally:
            self._exit_comp_scope(comp_names)

    def _gen_generator_expression(self, expr: TpyGeneratorExpression) -> str:
        gen = expr.generator
        elem_type = self._resolve_int_literal(expr.result_elem_type)
        cpp_elem = self.types.type_to_cpp(elem_type)
        cpp_var = escape_cpp_name(gen.var)

        comp_names = self._enter_comp_scope(gen)

        stmt_ind = INDENT * self.ctx.indent_level
        ind1 = stmt_ind + INDENT
        ind2 = ind1 + INDENT
        ind3 = ind2 + INDENT

        try:
            iterable_code = self.gen_expr_deref(gen.iterable)
            iterable_type = self.types.get_resolved_type(gen.iterable)
            yield_code = self.gen_expr_deref(expr.element_expr, elem_type)

            sema_elem = iterable_type.get_iteration_element_type()
            if sema_elem is not None and isinstance(sema_elem, IntLiteralType):
                sema_elem = self.ctx.analyzer.ctx.default_int_type
            if sema_elem is None:
                sema_elem = self.ctx.analyzer.ctx.default_int_type

            buf = io.StringIO()

            is_range = isinstance(gen.iterable, TpyCall) and gen.iterable.func == "range"
            nargs = len(gen.iterable.args) if is_range else 0

            # Simple range (1 or 2 args): counter state fits in lambda init-captures
            if is_range and nargs <= 2:
                self._gen_genexpr_counter_lambda(buf, gen, sema_elem, cpp_var, cpp_elem,
                                                 yield_code, stmt_ind, ind1, ind2, ind3)
                return buf.getvalue()

            # Begin/end sources (3-arg range, containers): wrap in IIFE so that
            # begin/end iterators are computed before the lambda and captured by
            # value into the lambda's init-capture list.
            is_lvalue = self._comp_is_lvalue(gen.iterable)
            buf.write(f"[&]() {{\n")

            if is_range:
                cpp_iter = sema_elem.to_cpp()
                start_code = self.gen_expr_deref(gen.iterable.args[0], sema_elem)
                stop_code = self.gen_expr_deref(gen.iterable.args[1], sema_elem)
                step_code = self.gen_expr_deref(gen.iterable.args[2], sema_elem)
                buf.write(f"{ind1}auto __src = ::tpy::Range<{cpp_iter}>({start_code}, {stop_code}, {step_code});\n")
            elif is_lvalue:
                buf.write(f"{ind1}auto& __src = {iterable_code};\n")
            else:
                buf.write(f"{ind1}auto __src = {iterable_code};\n")

            buf.write(f"{ind1}return ::tpy::make_generator<{cpp_elem}>(\n")

            ind2i = ind2 + INDENT
            ind3i = ind2i + INDENT
            # Capture begin/end by value in the lambda's init-capture
            buf.write(f"{ind2}[__beg = __src.begin(), __end = __src.end()]"
                      f"() mutable -> std::optional<{cpp_elem}> {{\n")
            buf.write(f"{ind2i}while (__beg != __end) {{\n")

            if gen.unpack_vars is not None:
                assert isinstance(sema_elem, TupleType)
                self.ctx.unpack_counter += 1
                tmp = f"__tup_{self.ctx.unpack_counter}"
                buf.write(f"{ind3i}const auto& {tmp} = *__beg++;\n")
                for i, uvar in enumerate(gen.unpack_vars):
                    if uvar is None:
                        continue
                    utype = sema_elem.element_types[i]
                    cpp_utype = self.types.type_to_cpp(utype)
                    cpp_name = escape_cpp_name(uvar)
                    if utype.is_value_type():
                        buf.write(f"{ind3i}{cpp_utype} {cpp_name} = std::get<{i}>({tmp});\n")
                    else:
                        buf.write(f"{ind3i}const auto& {cpp_name} = std::get<{i}>({tmp});\n")
            elif sema_elem.is_value_type():
                cpp_iter_elem = sema_elem.to_cpp()
                buf.write(f"{ind3i}{cpp_iter_elem} {cpp_var} = *__beg++;\n")
            else:
                buf.write(f"{ind3i}auto&& {cpp_var} = *__beg++;\n")

            self._gen_genexpr_yield(buf, gen, yield_code, cpp_elem, ind3i, ind3i + INDENT)
            buf.write(f"{ind2i}}}\n")
            buf.write(f"{ind2i}return std::nullopt;\n")
            buf.write(f"{ind2}}}\n")
            buf.write(f"{ind1});\n")
            buf.write(f"{stmt_ind}}}()")
            return buf.getvalue()
        finally:
            self._exit_comp_scope(comp_names)

    def _gen_genexpr_counter_lambda(
        self, buf: io.StringIO, gen: TpyComprehensionGenerator,
        elem_type: TpyType, cpp_var: str, cpp_elem: str,
        yield_code: str,
        stmt_ind: str, ind1: str, ind2: str, ind3: str,
    ) -> None:
        """Generate make_generator with counter-based lambda for range(N) or range(start, stop)."""
        range_call = gen.iterable
        assert isinstance(range_call, TpyCall)
        nargs = len(range_call.args)
        cpp_iter = elem_type.to_cpp()

        if nargs == 1:
            stop_code = self.gen_expr_deref(range_call.args[0], elem_type)
            captures = f"__i = {cpp_iter}(0), __stop = static_cast<{cpp_iter}>({stop_code})"
        else:
            start_code = self.gen_expr_deref(range_call.args[0], elem_type)
            stop_code = self.gen_expr_deref(range_call.args[1], elem_type)
            captures = f"__i = static_cast<{cpp_iter}>({start_code}), __stop = static_cast<{cpp_iter}>({stop_code})"

        buf.write(f"::tpy::make_generator<{cpp_elem}>(\n")
        buf.write(f"{ind1}[&, {captures}]() mutable -> std::optional<{cpp_elem}> {{\n")
        buf.write(f"{ind2}while (__i < __stop) {{\n")
        buf.write(f"{ind3}{cpp_iter} {cpp_var} = __i++;\n")
        self._gen_genexpr_yield(buf, gen, yield_code, cpp_elem, ind3, ind3 + INDENT)
        buf.write(f"{ind2}}}\n")
        buf.write(f"{ind2}return std::nullopt;\n")
        buf.write(f"{ind1}}}\n")
        buf.write(f"{stmt_ind})")

    def _gen_genexpr_yield(
        self, buf: io.StringIO, gen: TpyComprehensionGenerator,
        yield_code: str, cpp_elem: str,
        ind: str, ind_inner: str,
    ) -> None:
        """Generate the yield statement, optionally wrapped in filter conditions."""
        if gen.conditions:
            cond_parts = [self.gen_truthy_expr(c) for c in gen.conditions]
            cond_str = " && ".join(cond_parts)
            buf.write(f"{ind}if ({cond_str}) {{\n")
            buf.write(f"{ind_inner}return std::optional<{cpp_elem}>({yield_code});\n")
            buf.write(f"{ind}}}\n")
        else:
            buf.write(f"{ind}return std::optional<{cpp_elem}>({yield_code});\n")

    def _resolve_int_literal(self, typ: TpyType | None) -> TpyType:
        assert typ is not None
        if isinstance(typ, IntLiteralType):
            return self.ctx.analyzer.ctx.default_int_type
        return typ

    def _gen_comprehension_iife(
        self, gen: TpyComprehensionGenerator,
        container_type: str, insert_stmt: str,
        skip_reserve: bool,
    ) -> str:
        """Generate comprehension as IIFE: [&]() { container; loop; return; }()"""
        cpp_var = escape_cpp_name(gen.var)

        stmt_ind = INDENT * self.ctx.indent_level
        ind1 = stmt_ind + INDENT
        ind2 = ind1 + INDENT
        ind3 = ind2 + INDENT

        buf = io.StringIO()
        buf.write(f"[&]() {{\n")
        buf.write(f"{ind1}{container_type} __result;\n")

        iterable_code = self.gen_expr_deref(gen.iterable)
        iterable_type = self.types.get_resolved_type(gen.iterable)

        sema_elem = iterable_type.get_iteration_element_type()
        if sema_elem is not None and isinstance(sema_elem, IntLiteralType):
            sema_elem = self.ctx.analyzer.ctx.default_int_type
        if sema_elem is None:
            sema_elem = self.ctx.analyzer.ctx.default_int_type  # fallback; sema should reject non-iterables

        if isinstance(gen.iterable, TpyCall) and gen.iterable.func == "range":
            self._gen_comp_range_loop(buf, gen, sema_elem, ind1, ind2, cpp_var,
                                      iterable_code, skip_reserve=skip_reserve)
        else:
            self._gen_comp_begin_end_loop(buf, gen, sema_elem, ind1, ind2, cpp_var,
                                          iterable_code, skip_reserve=skip_reserve)

        if gen.conditions:
            cond_parts = [self.gen_truthy_expr(c) for c in gen.conditions]
            cond_str = " && ".join(cond_parts)
            buf.write(f"{ind2}if ({cond_str}) {{\n")
            buf.write(f"{ind3}{insert_stmt};\n")
            buf.write(f"{ind2}}}\n")
        else:
            buf.write(f"{ind2}{insert_stmt};\n")

        buf.write(f"{ind1}}}\n")
        buf.write(f"{ind1}return __result;\n")
        buf.write(f"{stmt_ind}}}()")

        return buf.getvalue()

    def _gen_comp_begin_end_loop(self, buf: io.StringIO, gen: TpyComprehensionGenerator,
                                  elem_type: TpyType, ind1: str, ind2: str,
                                  cpp_var: str, iterable_code: str,
                                  skip_reserve: bool = False) -> None:
        """Generate begin/end loop header for comprehension."""
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1

        # Determine lvalue vs rvalue binding
        is_lvalue = self._comp_is_lvalue(gen.iterable)
        obj_binding = "auto&" if is_lvalue else "auto"

        buf.write(f"{ind1}{obj_binding} __obj_{n} = {iterable_code};\n")
        iterable_type = self.types.get_resolved_type(gen.iterable)
        if not skip_reserve and self._is_sized_type(iterable_type):
            buf.write(f"{ind1}__result.reserve(__obj_{n}.size());\n")
        buf.write(f"{ind1}auto __beg_{n} = __obj_{n}.begin();\n")
        buf.write(f"{ind1}auto __end_{n} = __obj_{n}.end();\n")
        buf.write(f"{ind1}for (; __beg_{n} != __end_{n}; ++__beg_{n}) {{\n")

        if gen.unpack_vars is not None:
            self._gen_comp_tuple_unpack(buf, gen, elem_type, ind2, n)
        elif elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            buf.write(f"{ind2}{cpp_elem} {cpp_var} = *__beg_{n};\n")
        else:
            buf.write(f"{ind2}auto&& {cpp_var} = *__beg_{n};\n")

    def _gen_comp_tuple_unpack(self, buf: io.StringIO, gen: TpyComprehensionGenerator,
                                elem_type: TpyType, ind: str, iter_n: int) -> None:
        """Generate tuple unpacking bindings inside comprehension loop body."""
        assert gen.unpack_vars is not None
        assert isinstance(elem_type, TupleType)
        self.ctx.unpack_counter += 1
        tmp = f"__tup_{self.ctx.unpack_counter}"
        buf.write(f"{ind}const auto& {tmp} = *__beg_{iter_n};\n")
        for i, uvar in enumerate(gen.unpack_vars):
            if uvar is None:
                continue
            utype = elem_type.element_types[i]
            cpp_type = self.types.type_to_cpp(utype)
            cpp_name = escape_cpp_name(uvar)
            if utype.is_value_type():
                buf.write(f"{ind}{cpp_type} {cpp_name} = std::get<{i}>({tmp});\n")
            else:
                buf.write(f"{ind}const auto& {cpp_name} = std::get<{i}>({tmp});\n")

    def _gen_comp_range_loop(self, buf: io.StringIO, gen: TpyComprehensionGenerator,
                              elem_type: TpyType, ind1: str, ind2: str,
                              cpp_var: str, iterable_code: str,
                              skip_reserve: bool = False) -> None:
        """Generate counter loop for range() in comprehension."""
        range_call = gen.iterable
        assert isinstance(range_call, TpyCall)
        nargs = len(range_call.args)
        cpp_elem = elem_type.to_cpp()
        is_bigint = isinstance(elem_type, BigIntType)

        if nargs == 1:
            stop_expr = self.gen_expr_deref(range_call.args[0], elem_type)
            is_literal = isinstance(range_call.args[0], TpyIntLiteral)
            if is_literal:
                stop_var = stop_expr
            else:
                n = self.ctx.iter_counter
                self.ctx.iter_counter += 1
                stop_var = f"__stop_{n}"
                buf.write(f"{ind1}const {cpp_elem} {stop_var} = {stop_expr};\n")
            if not skip_reserve:
                if is_bigint:
                    buf.write(f"{ind1}{{ size_t __sz; if ({stop_var}.to_size_checked(__sz)) __result.reserve(__sz); }}\n")
                else:
                    buf.write(f"{ind1}if ({stop_var} > 0) __result.reserve(static_cast<size_t>({stop_var}));\n")
            buf.write(f"{ind1}for ({cpp_elem} {cpp_var} = 0; {cpp_var} < {stop_var}; ++{cpp_var}) {{\n")
        elif nargs == 2:
            start_expr = self.gen_expr_deref(range_call.args[0], elem_type)
            stop_expr = self.gen_expr_deref(range_call.args[1], elem_type)
            start_lit = isinstance(range_call.args[0], TpyIntLiteral)
            stop_lit = isinstance(range_call.args[1], TpyIntLiteral)
            if start_lit:
                start_var = start_expr
            else:
                n = self.ctx.iter_counter
                self.ctx.iter_counter += 1
                start_var = f"__start_{n}"
                buf.write(f"{ind1}const {cpp_elem} {start_var} = {start_expr};\n")
            if stop_lit:
                stop_var = stop_expr
            else:
                n = self.ctx.iter_counter
                self.ctx.iter_counter += 1
                stop_var = f"__stop_{n}"
                buf.write(f"{ind1}const {cpp_elem} {stop_var} = {stop_expr};\n")
            if not skip_reserve:
                if is_bigint:
                    buf.write(f"{ind1}if ({stop_var} > {start_var}) {{ size_t __sz; if (({stop_var} - {start_var}).to_size_checked(__sz)) __result.reserve(__sz); }}\n")
                else:
                    buf.write(f"{ind1}if ({stop_var} > {start_var}) __result.reserve(static_cast<size_t>({stop_var} - {start_var}));\n")
            buf.write(f"{ind1}for ({cpp_elem} {cpp_var} = {start_var}; {cpp_var} < {stop_var}; ++{cpp_var}) {{\n")
        else:
            # 3-arg range: fall back to Range<T> begin/end
            self._gen_comp_begin_end_loop(buf, gen, elem_type, ind1, ind2, cpp_var,
                                          iterable_code, skip_reserve=skip_reserve)
            return

    def _enter_comp_scope(self, gen: TpyComprehensionGenerator) -> set[str]:
        """Register comprehension loop variables so they shadow globals in codegen."""
        names: set[str] = {gen.var}
        if gen.unpack_vars:
            names.update(v for v in gen.unpack_vars if v is not None)
        self.ctx.comp_local_names |= names
        return names

    def _exit_comp_scope(self, names: set[str]) -> None:
        # Safe as set-difference because sema rejects nested comprehensions,
        # so no name can appear in two active comp scopes simultaneously.
        self.ctx.comp_local_names -= names

    def _comp_is_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an iterable expression is a C++ lvalue (for comprehensions).

        Mirrors _is_lvalue_iterable in StatementGenerator.
        """
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyFieldAccess):
            return self._comp_is_lvalue(expr.obj)
        if isinstance(expr, TpySubscript):
            return self._comp_is_lvalue(expr.obj)
        if isinstance(expr, (TpyMethodCall, TpyCall)):
            if isinstance(expr, TpyCall) and expr.call_type is not None:
                return False
            if isinstance(expr, TpyCall) and self.ctx.analyzer.registry.get_record(expr.func):
                return False
            ret_type = self.types.get_resolved_type(expr)
            return not ret_type.is_value_type() and not isinstance(ret_type, OptionalType)
        return False

    @staticmethod
    def _is_sized_type(typ: TpyType) -> bool:
        """Check if a type has .size() in C++ (all STL containers)."""
        typ = unwrap_readonly(typ)
        return isinstance(typ, (ListType, ArrayType, SpanType, DictType, SetType,
                                DictKeysViewType, DictValuesViewType,
                                DictItemsViewType))

    def _wrap_for_owned_slot(self, code: str, resolved: TpyType, slot_type: TpyType | None) -> str:
        """Wrap a str-view expression with std::string() when placed in an owned-str slot.

        Container elements (list, dict, set, tuple str slots) must be owned std::string.
        A string_view variable that ends up in such a slot is copied at the call site
        rather than promoting the variable's type to std::string for its whole lifetime.
        """
        if isinstance(resolved, StrViewType):
            if isinstance(slot_type, StrType):
                return f"std::string({code})"
            if isinstance(slot_type, OptionalType) and isinstance(slot_type.inner, StrType):
                return f"std::string({code})"
        return code

    def _gen_tuple_literal(self, expr: TpyTupleLiteral, target_type: TpyType | None) -> str:
        """Generate tuple literal code."""
        target_tuple = target_type if isinstance(target_type, TupleType) else None
        resolved_elem_types = []
        elem_strs = []
        for i, elem in enumerate(expr.elements):
            elem_target = target_tuple.element_types[i] if target_tuple and i < len(target_tuple.element_types) else None
            resolved = self.types.get_resolved_type(elem, elem_target)
            elem_str = self._wrap_for_owned_slot(self.gen_expr_deref(elem, elem_target), resolved, elem_target)
            # Use elem_target for the tuple type when a target was given: the code was
            # generated with that target in mind, so the C++ expression's type is elem_target.
            effective_type = elem_target if elem_target is not None else resolved
            resolved_elem_types.append(effective_type)
            elem_strs.append(elem_str)
        has_ref_elements = any(
            (i < len(expr.elem_capture) and expr.elem_capture[i] != TupleElemCapture.VALUE)
            or isinstance(resolved_elem_types[i], TypeParamRef)
            or (not expr.elem_capture and not resolved_elem_types[i].is_value_type()
                and not isinstance(resolved_elem_types[i], OwnType))
            for i in range(len(resolved_elem_types))
        )
        if has_ref_elements:
            cpp_type = self._tuple_literal_cpp_type(resolved_elem_types, expr)
        else:
            cpp_type = self.types.type_to_cpp(TupleType(tuple(resolved_elem_types)))
        return f"{cpp_type}{{{', '.join(elem_strs)}}}"

    def _tuple_literal_cpp_type(
        self,
        resolved_elem_types: list[TpyType],
        expr: TpyTupleLiteral,
    ) -> str:
        """Build C++ tuple type for a literal, using sema-annotated elem_capture."""
        cpp_parts: list[str] = []
        for i, et in enumerate(resolved_elem_types):
            base = self.types.type_to_cpp(et)
            if i < len(expr.elem_capture):
                mode = expr.elem_capture[i]
            elif not et.is_value_type() and not isinstance(et, OwnType):
                # No sema annotation (e.g. tuple in list literal or call arg).
                # Use REF only for simple lvalues (variables, field access).
                # Rvalues and subscripts get VALUE to avoid binding issues
                # (e.g. Span[readonly[T]] subscript returns const ref).
                if _is_simple_lvalue(expr.elements[i]):
                    mode = TupleElemCapture.REF
                else:
                    mode = TupleElemCapture.VALUE
            else:
                mode = TupleElemCapture.VALUE
            if mode == TupleElemCapture.REF:
                cpp_parts.append(f"{base}&")
            elif mode == TupleElemCapture.CONST_REF:
                cpp_parts.append(f"const {base}&")
            elif isinstance(et, TypeParamRef):
                # Defer value-vs-ref to C++ instantiation time
                cpp_parts.append(f"::tpy::val_or_ref_t<{base}>")
            else:
                cpp_parts.append(base)
        return f"std::tuple<{', '.join(cpp_parts)}>"

    def _gen_subscript(self, expr: TpySubscript) -> str:
        """Generate subscript code."""
        # Enum name lookup: Color["Red"] -> ::tpy::EnumUtil<Color>::from_name("Red")
        if expr.enum_from_name is not None:
            cpp_type = expr.enum_from_name.to_cpp()
            index = self.gen_expr(expr.index)
            return f"::tpy::EnumUtil<{cpp_type}>::from_name({index})"

        obj = self.gen_expr(expr.obj)

        # Narrowed value-Optional: sema sees T but C++ var is still std::optional<T>
        obj = self._maybe_unwrap_narrowed_optional(
            expr.obj, obj, self.ctx.is_indirect_name(expr.obj))

        # Slice: obj[start:stop]
        if isinstance(expr.index, TpySlice):
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
            if expr.user_slice_getitem:
                return self._gen_user_slice(subscript_obj, expr.index)
            return self._gen_slice(subscript_obj, expr.index, expr.obj)

        obj_type = self.types.get_resolved_type(expr.obj)

        # When narrowed from Optional, use inner type for method lookup
        if isinstance(obj_type, OptionalType) and not obj_type.uses_pointer_repr():
            analyzed = self.ctx.get_expr_type(expr.obj)
            if not isinstance(analyzed, OptionalType):
                obj_type = obj_type.inner

        # Tuple subscript: std::get<N>(obj)
        if isinstance(unwrap_readonly(obj_type), TupleType):
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
            idx = self._extract_compile_time_index(expr.index)
            tuple_type = unwrap_readonly(obj_type)
            n = len(tuple_type.element_types)
            if idx < 0:
                idx += n
            return f"std::get<{idx}>({subscript_obj})"

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
                subscript_obj = f"::tpy::deref_optional_check({obj})"
            else:
                # For pointer-globals with wrapper storage, this yields raw `T*`.
                ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
                subscript_obj = f"::tpy::deref_check({ptr_expr})"
        index_expr = self.gen_index_expr(expr.index, index_type)

        # Bounds-safe: index provably in [0, len(obj)), skip normalize_index
        if expr.bounds_safe:
            return f"{subscript_obj}[{index_expr}]"

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
    def _extract_compile_time_index(index: TpyExpr) -> int:
        """Extract compile-time integer index from an expression (validated by sema)."""
        if isinstance(index, TpyIntLiteral):
            return index.value
        if isinstance(index, TpyUnaryOp) and index.op == "-" and isinstance(index.operand, TpyIntLiteral):
            return -index.operand.value
        raise RuntimeError(f"Expected compile-time integer index, got {type(index).__name__}")

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


    def _gen_slice(self, obj: str, sl: TpySlice, obj_expr: TpyExpr) -> str:
        """Generate slice: str_slice for strings, list_slice for containers."""
        start = self._gen_slice_bound(sl.lower) if sl.lower is not None else "0"
        stop = self._gen_slice_bound(sl.upper) if sl.upper is not None else "::tpy::SLICE_END"
        obj_type = self.types.get_resolved_type(obj_expr)
        # get_resolved_type returns the C++ declared type, which stays
        # Optional even after narrowing. Use sema's analyzed type to
        # detect the unwrapped inner type for correct dispatch.
        if isinstance(obj_type, OptionalType):
            analyzed = self.ctx.get_expr_type(obj_expr)
            if not isinstance(analyzed, OptionalType):
                obj_type = obj_type.inner
        if is_any_str_type(obj_type):
            return f"::tpy::str_slice({obj}, {start}, {stop})"
        return f"::tpy::list_slice({obj}, {start}, {stop})"

    def _gen_slice_bound(self, expr: TpyExpr) -> str:
        """Generate a slice bound expression, converting to int32_t if needed."""
        index_type = self.ctx.analyzer.get_expr_type(expr)
        code = self.gen_expr_deref(expr)
        if self.types.is_runtime_bigint(expr, index_type):
            code = f"{code}.to_fixed_check<int32_t>()"
        return code

    def _gen_user_slice(self, obj: str, sl: TpySlice) -> str:
        """Generate user-type slice via direct __getitem__ call."""
        start = self._gen_optional_slice_bound(sl.lower)
        stop = self._gen_optional_slice_bound(sl.upper)
        return f"{obj}.__getitem__(::tpy::Slice{{{start}, {stop}}})"

    def _gen_optional_slice_bound(self, expr: TpyExpr | None) -> str:
        """Generate an optional slice bound for ::tpy::Slice construction."""
        if expr is None:
            return "std::nullopt"
        return self._gen_slice_bound(expr)

    def _gen_binop_from_result(self, binop_result: ResolvedBinop,
                               left: str, right: str) -> str:
        """Generate binary operation code from a ResolvedBinop.

        Applies wrappers to operands and substitutes into the method template.
        Handles is_reverse flag for reverse operators (__radd__, etc.).
        """
        wrapped_left = binop_result.left_wrapper.replace("{self}", left).replace("{expr}", left)
        wrapped_right = binop_result.right_wrapper.replace("{self}", right).replace("{expr}", right)
        method = binop_result.method
        cpp_tmpl = method.cpp_template
        if not cpp_tmpl and method.native_function and method.native_name:
            cpp_tmpl = f"{qualify_native_name(method.native_name)}({{self}}, {{0}})"
        if binop_result.is_reverse:
            return expand_cpp_template(cpp_tmpl, wrapped_right, wrapped_left)
        else:
            return expand_cpp_template(cpp_tmpl, wrapped_left, wrapped_right)

    def _gen_span_coercion(self, expr: TpyExpr, span_type: SpanType, gen_inner: str) -> str:
        """Generate std::span conversion for supported container types."""
        # Span[T] -> Span[readonly[T]]: C++ implicit conversion, no helper needed
        actual_type = self.ctx.get_expr_type(expr)
        if isinstance(actual_type, SpanType):
            return gen_inner
        # ReadOnlySpanLike[T] protocol type: always uses as_span (readonly)
        if is_protocol_type(actual_type) and actual_type.qualified_name() == "tpy.ReadOnlySpanLike":
            return f"::tpy::as_span({gen_inner})"
        # User type with __span__() method: call it directly
        if isinstance(actual_type, NamedType) and actual_type.is_user_record:
            if builtin_modules.get_span_element_type(actual_type, registry=self.ctx.analyzer.registry) is not None:
                if self.ctx.is_indirect_name(expr):
                    gen_inner = f"(*{gen_inner})"
                return f"{gen_inner}.__span__()"
        helper = "::tpy::as_span" if span_type.is_readonly else "::tpy::as_mut_span"
        if isinstance(expr, TpyArrayLiteral):
            expected_array_type = ArrayType(span_type.element_type, len(expr.elements))
            array_expr = f"{expected_array_type.to_cpp()}{gen_inner}"
            return f"{helper}({array_expr})"
        # gen_inner already generated, need to check if source was global
        if self.ctx.is_indirect_name(expr):
            gen_inner = f"(*{gen_inner})"
        return f"{helper}({gen_inner})"

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

    @staticmethod
    def _container_to_str(arg_type: TpyType, gen_arg: str) -> str | None:
        """Return a to_str call for container types, or None."""
        tmpl = container_to_str_template(arg_type)
        if tmpl is not None:
            return tmpl.replace("{0}", gen_arg)
        return None

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

                container_str = self._container_to_str(arg_type, gen_arg)

                # !r conversion: always wrap with __repr__
                if container_str is not None:
                    gen_arg = container_str
                elif conv == FSTRING_CONV_REPR:
                    gen_arg = f"::tpy::__repr__({gen_arg})"
                # !s conversion on user types: wrap with __str__
                elif conv == FSTRING_CONV_STR and is_user_type:
                    gen_arg = f"::tpy::__str__({gen_arg})"
                # Wrap args that need Python-compatible formatting
                elif isinstance(arg_type, BoolType):
                    if has_spec:
                        gen_arg = f"static_cast<int>({gen_arg})"
                    else:
                        gen_arg = f"::tpy::bool_to_str({gen_arg})"
                elif isinstance(arg_type, FloatType) and not has_spec:
                    gen_arg = f"::tpy::float_to_str({gen_arg})"
                elif isinstance(arg_type, Float32Type) and not has_spec:
                    gen_arg = f"::tpy::float_to_str(static_cast<double>({gen_arg}))"
                elif self.types.is_runtime_bigint(part.expr, arg_type) and not has_spec:
                    gen_arg = f"({gen_arg}).to_string()"
                elif isinstance(arg_type, FixedIntType) and arg_type.bits == 8:
                    gen_arg = f"static_cast<int>({gen_arg})"
                elif isinstance(arg_type, EnumType):
                    gen_arg = f"static_cast<int>({gen_arg})"
                elif is_user_type:
                    gen_arg = f"::tpy::__str__({gen_arg})"

                args.append(gen_arg)

        if all_literal:
            # Pure literal f-string -- use raw parts (no brace-escaping needed)
            return f'std::string("{"".join(raw_parts)}")'

        fmt_str = "".join(fmt_parts)
        args_str = ", ".join(args)
        return f'std::format("{fmt_str}", {args_str})'

    def _gen_named_expr(self, expr: TpyNamedExpr) -> str:
        """Generate walrus operator: (x := value) -> inline C++ assignment."""
        value_type = self.types.get_resolved_type(expr)
        cpp_name = escape_cpp_name(expr.target)
        value_code = self.gen_expr(expr.value, value_type)

        # Emit pre-declaration only once per function (walrus_pre_declared
        # is not snapshot/restored across branches, unlike declared_vars)
        need_predecl = expr.target not in self.ctx.walrus_pre_declared
        if need_predecl:
            self.ctx.walrus_pre_declared.add(expr.target)
            cpp_type = self.types.type_to_cpp(value_type)

            if isinstance(value_type, OptionalType) and value_type.uses_pointer_repr():
                inner_cpp = self.types.type_to_cpp(value_type.inner)
                self.ctx.temps.declare_named(cpp_name, f"{inner_cpp}*", init="nullptr")
                self.ctx.pointer_locals.add(expr.target)
            elif value_type.is_value_type() or isinstance(value_type, OptionalType):
                self.ctx.temps.declare_named(cpp_name, cpp_type)
            else:
                # Non-value, non-Optional: std::optional<T> x; + comma for T& result
                self.ctx.temps.declare_named(cpp_name, f"std::optional<{cpp_type}>")
                self.ctx.narrowed_vars[expr.target] = f"(*{cpp_name})"

        # Always ensure tracking state is set (may have been cleared by
        # restore_local_scope between branches)
        self.ctx.declared_vars.add(expr.target)
        self.ctx.local_scope_names.add(expr.target)
        self.ctx.var_types[expr.target] = value_type
        if isinstance(value_type, OptionalType) and value_type.uses_pointer_repr():
            self.ctx.pointer_locals.add(expr.target)

        if not value_type.is_value_type() and not isinstance(value_type, OptionalType):
            return f"({cpp_name} = {value_code}, *{cpp_name})"
        return f"({cpp_name} = {value_code})"

    def _gen_if_expr(self, expr: TpyIfExpr,
                     target_type: TpyType | None = None) -> str:
        """Generate C++ ternary: (cond) ? (then_expr) : (else_expr)."""
        result_type = self.types.get_resolved_type(expr)
        is_ptr_optional = (
            isinstance(result_type, OptionalType) and result_type.uses_pointer_repr()
        )
        cond = self.gen_truthy_expr(expr.condition)

        # Use the result type as the branch target so that:
        # - None literals get OptionalType target -> generate std::nullopt
        # - Narrowed Optional vars get non-Optional target -> gen_expr_deref unwraps
        branch_target = result_type or target_type

        # For pointer-repr Optional, use gen_expr (keeps raw T*) instead of
        # gen_expr_deref (which dereferences pointer_locals to (*var)).
        gen_branch = self.gen_expr if is_ptr_optional else self.gen_expr_deref

        # Then-branch: apply Union isinstance narrowing from condition
        then_facts = self._collect_inline_isinstance_facts(
            expr.condition, true_branch=True)
        saved_then: dict[str, str | None] = {}
        for var_name, inline_expr in then_facts.items():
            saved_then[var_name] = self.ctx.narrowed_vars.get(var_name)
            self.ctx.narrowed_vars[var_name] = inline_expr
        then_code = gen_branch(expr.then_expr, branch_target)
        for var_name, prev in saved_then.items():
            if prev is not None:
                self.ctx.narrowed_vars[var_name] = prev
            else:
                self.ctx.narrowed_vars.pop(var_name, None)

        # Else-branch: apply negated narrowing from condition
        else_facts = self._collect_inline_isinstance_facts(
            expr.condition, true_branch=False)
        saved_else: dict[str, str | None] = {}
        for var_name, inline_expr in else_facts.items():
            saved_else[var_name] = self.ctx.narrowed_vars.get(var_name)
            self.ctx.narrowed_vars[var_name] = inline_expr
        else_code = gen_branch(expr.else_expr, branch_target)
        for var_name, prev in saved_else.items():
            if prev is not None:
                self.ctx.narrowed_vars[var_name] = prev
            else:
                self.ctx.narrowed_vars.pop(var_name, None)

        # C++ can't deduce template params from bare initializer lists,
        # so array literal branches need explicit std::vector<T>{...} prefix.
        if isinstance(result_type, ListType):
            cpp_type = self.types.type_to_cpp(result_type)
            if isinstance(expr.then_expr, TpyArrayLiteral):
                then_code = f"{cpp_type}{then_code}"
            if isinstance(expr.else_expr, TpyArrayLiteral):
                else_code = f"{cpp_type}{else_code}"

        # C++ ternary requires both branches to have the same type.
        # When arms have mismatched C++ types (one string_view, one std::string),
        # explicitly convert the string_view arm so the ternary deduces std::string.
        # Skip when the target is StrViewType -- wrapping would create a dangling
        # string_view pointing to a temporary std::string.
        if isinstance(result_type, StrType) and not isinstance(target_type, StrViewType):
            then_is_view = self._is_str_view_at_runtime(expr.then_expr)
            else_is_view = self._is_str_view_at_runtime(expr.else_expr)
            if then_is_view != else_is_view:
                # String literals are const char* in ternary context, not string_view.
                # C++ resolves string ? const-char* natively, no explicit wrap needed.
                if then_is_view and not isinstance(expr.then_expr, TpyStrLiteral):
                    then_code = f"std::string({then_code})"
                if else_is_view and not isinstance(expr.else_expr, TpyStrLiteral):
                    else_code = f"std::string({else_code})"

        if isinstance(result_type, OptionalType):
            if is_ptr_optional:
                # Pointer-repr Optional (T*): produce T* for each branch.
                # None -> nullptr; non-pointer lvalue -> &(expr).
                then_code = self._ptr_optional_branch(
                    expr.then_expr, then_code)
                else_code = self._ptr_optional_branch(
                    expr.else_expr, else_code)
            else:
                # Value-repr Optional (std::optional<T>): branches may have
                # mismatched types (std::nullopt vs T) -- wrap each in
                # explicit Optional for C++ ternary type deduction.
                cpp_type = self.types.type_to_cpp(result_type)
                then_code = f"{cpp_type}({then_code})"
                else_code = f"{cpp_type}({else_code})"

        return f"(({cond}) ? ({then_code}) : ({else_code}))"

    def _ptr_optional_branch(self, branch_expr: TpyExpr, code: str) -> str:
        """Convert a ternary branch to T* for pointer-repr Optional results."""
        if isinstance(branch_expr, TpyNoneLiteral):
            return "nullptr"
        branch_type = self.ctx.get_expr_type(branch_expr)
        if isinstance(branch_type, (OptionalType, PtrType)):
            return code
        return f"&({code})"

    def _gen_lambda(self, expr: TpyLambda) -> str:
        """Generate a C++ lambda expression from a TpyLambda."""
        params = []
        for pname, ptype in zip(expr.param_names, expr.inferred_param_types):
            cpp_type = ptype.to_cpp_param(escape_cpp_name(pname))
            params.append(cpp_type)
        params_str = ", ".join(params)

        # Track lambda params as locals so they're not treated as global pointer slots
        saved_locals = self.ctx.local_scope_names.copy()
        for pname in expr.param_names:
            self.ctx.local_scope_names.add(pname)
        try:
            body_code = self.gen_expr(expr.body, expr.inferred_return_type)
        finally:
            self.ctx.local_scope_names = saved_locals

        if expr.captured_names:
            refs = ", ".join(f"&{escape_cpp_name(n)}" for n in expr.captured_names)
            capture = f"[{refs}]"
        else:
            capture = "[]"
        if isinstance(expr.inferred_return_type, VoidType):
            return f"{capture}({params_str}) {{ {body_code}; }}"
        return f"{capture}({params_str}) {{ return {body_code}; }}"
