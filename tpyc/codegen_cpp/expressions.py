"""
TurboPython Expression Code Generation

Generates C++ code from TurboPython expressions.
"""

from __future__ import annotations
import contextlib
import io
from dataclasses import replace as dc_replace
from typing import Final, TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, LiteralType, LiteralValue,
    NominalType, PtrType, OwnType, OptionalType, NoneType, AnyType, make_array,
    PendingListType, ListRepeatType,
    TypeParamRef, ReadonlyType, unwrap_readonly, unwrap_qualifiers, unwrap_optional_own, UnionType, VoidType, make_union, union_none_narrow,
    TupleType, CallableType,
    INT32, BIGINT, FLOAT, CHAR, VOID, is_protocol_type, is_any_str_type, is_any_bytes_type, container_to_str_template,
    ResolvedBinop, get_covariant_params, unwrap_ref_type, RefType, ParamInfo,
    is_float_type, is_readonly_span)
from ..type_def_registry import (
    is_dict_view, is_set, is_dict, is_array, is_span, is_list,
    is_fixed_int_type, is_big_int_type, is_bool_type, is_char_type,
    is_str_type, is_string_type, is_str_view_type,
    is_bytes_type, is_bytes_view_type,
    is_float32_type, is_float64_type, is_bytearray_type,
    int_traits_of,
    is_enum_type, is_int_enum_type, enum_info_of,
    protocol_info_of,
)
from ..symbol_binding import lookup_qualified, lookup_imported, resolve_definer, SymbolKind


_CMP_HELPER: Final = {
    "<":  "::std::cmp_less",
    "<=": "::std::cmp_less_equal",
    ">":  "::std::cmp_greater",
    ">=": "::std::cmp_greater_equal",
    "==": "::std::cmp_equal",
    "!=": "::std::cmp_not_equal",
}


def _mixed_sign_fixed_int(left_type: "TpyType", right_type: "TpyType") -> bool:
    """True when comparing fixed-int operands with different signedness.

    Same-rank or otherwise sign-mixed comparisons trip -Wsign-compare under
    GCC's "usual arithmetic conversions". Codegen routes these through
    ``std::cmp_*`` (C++20) so the comparison is mathematically correct
    regardless of bit-pattern reinterpretation.
    """
    if not (is_fixed_int_type(left_type) and is_fixed_int_type(right_type)):
        return False
    lt = int_traits_of(left_type)
    rt = int_traits_of(right_type)
    if lt is None or rt is None:
        return False
    return lt.signed != rt.signed
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyTupleLiteral, TupleElemCapture, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression, TpyComprehensionGenerator,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr,
    TpyLambda,
    TpyVarargPack, TpyStarUnpack,
    collect_name_refs,
)
from ..prescan import match_is_none
from ..namespace import BindingKind
from ..sema.numeric_lattice import fixed_int_range_contains
from .context import INDENT, escape_cpp_string, escape_cpp_char, escape_cpp_name, qualified_cpp_name, qualify_native_name, enum_cpp_name, loop_var_binding, is_lvalue_iterable, cpp_string_literal_expr, cpp_bytes_literal_span, view_key_target
from .functions import literal_mangled_name
from .. import qnames

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .builtins import BuiltinGenerator
    from .protocols import ProtocolGenerator

from tpyc import modules as builtin_modules



def _flatten_chain(expr: TpyExpr, op: str) -> list[TpyExpr]:
    """Flatten a left-recursive &&/|| chain into a list of operands."""
    result: list[TpyExpr] = []
    while isinstance(expr, TpyBinOp) and expr.op == op:
        result.append(expr.right)
        expr = expr.left
    result.append(expr)
    result.reverse()
    return result


def _extract_literal_value(expr: TpyExpr) -> LiteralValue | None:
    """Extract a LiteralValue from a literal AST node."""
    if isinstance(expr, TpyStrLiteral):
        return LiteralValue("str", expr.value)
    if isinstance(expr, TpyBoolLiteral):
        return LiteralValue("bool", expr.value)
    if isinstance(expr, TpyIntLiteral):
        return LiteralValue("int", expr.value)
    if (isinstance(expr, TpyUnaryOp) and expr.op == "-"
            and isinstance(expr.operand, TpyIntLiteral)):
        return LiteralValue("int", -expr.operand.value)
    return None


def _check_literal_chain(
    expr: TpyBinOp, literal_facts: dict[str, TpyType],
) -> bool | None:
    """Check if a flattened &&/|| chain of == comparisons can be resolved.

    For ||: returns True if collected values cover the variable's full
    LiteralType set (full-set coverage).
    For &&: returns False if the same variable is required to equal two
    different values (contradiction).
    """
    operands = _flatten_chain(expr, expr.op)
    eq_facts: dict[str, set] = {}
    for operand in operands:
        if not isinstance(operand, TpyBinOp) or operand.op != "==":
            return None
        extracted = False
        for var_side, lit_side in [(operand.left, operand.right),
                                   (operand.right, operand.left)]:
            if not isinstance(var_side, TpyName):
                continue
            lit_val = _extract_literal_value(lit_side)
            if lit_val is None:
                continue
            eq_facts.setdefault(var_side.name, set()).add(lit_val)
            extracted = True
            break
        if not extracted:
            return None
    if expr.op == "||":
        for var_name, values in eq_facts.items():
            lit_type = literal_facts.get(var_name)
            if isinstance(lit_type, LiteralType) and set(lit_type.values) <= values:
                return True
    else:
        for values in eq_facts.values():
            if len(values) > 1:
                return False
    return None


def _check_literal_in(
    expr: TpyBinOp, literal_facts: dict[str, TpyType],
) -> bool | None:
    """Check if `x in (a, b, ...)` / `x not in (a, b, ...)` can be resolved."""
    if not isinstance(expr.left, TpyName):
        return None
    lit_type = literal_facts.get(expr.left.name)
    if not isinstance(lit_type, LiteralType):
        return None
    if not isinstance(expr.right, (TpyTupleLiteral, TpySetLiteral)):
        return None
    rhs_values: set = set()
    for elem in expr.right.elements:
        val = _extract_literal_value(elem)
        if val is None:
            return None
        rhs_values.add(val)
    lit_values = set(lit_type.values)
    is_in = expr.op == "in"
    if lit_values <= rhs_values:
        return True if is_in else False
    if lit_values.isdisjoint(rhs_values):
        return False if is_in else True
    return None


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


def _is_concrete_user_record(t: TpyType | None, registry) -> bool:
    """True for a user-defined record whose C++ operator[] takes the user's
    declared parameter type (typically int32_t), not size_t. Excludes built-in
    containers (their stubs have builtin_type_key, so is_user_record is
    False), protocols, unresolved placeholders, and @native records that wrap
    STL types (where operator[] is C++-defined, takes size_t).
    """
    if not (isinstance(t, NominalType) and t.is_user_record):
        return False
    record = registry.get_record_for_type(t)
    return record is not None and not record.is_native


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
            if self.ctx.is_already_pointer_source(arg):
                return self.gen_expr(arg, ptype)
            arg_expr_type = self.ctx.get_expr_type(arg)
            if isinstance(arg_expr_type, OptionalType):
                arg_gen = self.gen_expr(arg, ptype)
                if isinstance(arg, TpyFieldAccess):
                    return f"::tpy::optional_to_ptr({arg_gen})"
                return arg_gen
            gen = self.gen_expr(arg, ptype)
            if self.ctx.is_temporary_expr(arg):
                tmp = self.ctx.temps.create(arg_expr_type, gen) if arg_expr_type else self.ctx.temps.create_typed("auto", gen)
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
        # OPTIONAL_STORAGE arg (Own[Opt[P_ref]] param): C++ shape is
        # std::optional<P>&&, the slot wants const P*. Lift before the
        # is_indirect_name short-circuit -- the param is also in
        # pointer_locals (for arrow field access).
        if isinstance(arg, TpyName) and self.ctx.needs_optional_to_ptr_lift(arg.name):
            return f"::tpy::optional_to_ptr({self.gen_expr(arg, ptype)})"
        if self.ctx.is_storage_form_optional_source(arg):
            return f"::tpy::optional_to_ptr({self.gen_expr(arg, ptype)})"
        if self.ctx.is_already_pointer_source(arg):
            return self.gen_expr(arg, ptype)
        arg_type = self.ctx.get_expr_type(arg)
        if isinstance(arg_type, OptionalType):
            arg_gen = self.gen_expr(arg, ptype)
            # std::optional<T> -> T* conversion (generic return passed to concrete param)
            if not arg_type.uses_pointer_repr():
                return f"::tpy::optional_to_ptr({arg_gen})"
            return arg_gen
        gen = self.gen_expr(arg, ptype)
        if self.ctx.is_temporary_expr(arg):
            # Use the Optional's inner type for the temp -- gen was already
            # generated with ptype as target (e.g. list literal becomes
            # std::vector{...}), so the temp must match the param's inner
            # type, not the sema expression type (which can differ, e.g.
            # ArrayType for a fixed-size list literal).
            tmp = self.ctx.temps.create(actual.inner, gen)
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
        # Recursive union alias: NominalType("Expr") and UnionType(members) alias
        # the same C++ wrapper struct. When ptype is the expanded union and arg
        # carries the unexpanded NominalType (reverse source ordering -- classes
        # defined before alias), treat them as the same already-variant value.
        arg_is_same_recursive = (
            isinstance(arg_type, NominalType)
            and arg_type.name in self.ctx.recursive_union_names
            and self.ctx.recursive_union_name(ptype_union) == arg_type.name
        )
        already_union = (
            isinstance(arg_type, UnionType)
            or (cpp_decl is not None and isinstance(cpp_decl, UnionType))
            or arg_is_same_recursive
        )
        if self.ctx.is_ptr_variant_union(ptype_union):
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
            # VALUE_VARIANT source (Own[A|B] param) passed to a pointer-variant
            # slot: lift via to_ptr_variant. The Own wrapper makes the source
            # C++ shape value-variant; the slot wants pointer-variant. Mirror
            # of the Own[Optional] -> optional_to_ptr lift at
            # _gen_optional_ptr_arg.
            if isinstance(arg, TpyName) and self.ctx.needs_to_ptr_variant_lift(arg.name):
                return f"::tpy::to_ptr_variant({self.gen_expr_deref(arg)})"
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
        # @overload specialization: name narrowed to a non-Optional concrete type.
        # The physical C++ param already IS that concrete type; skip the Optional
        # auto-deref that would otherwise kick in for a declared Optional[T].
        if isinstance(expr, TpyName) and expr.name in self.ctx.overload_param_types:
            narrowed = self.ctx.overload_param_types[expr.name]
            if not isinstance(narrowed, OptionalType):
                return result
        is_narrowed = isinstance(expr, TpyName) and expr.name in self.ctx.narrowed_vars
        if self.ctx.is_indirect_name(expr) and not is_narrowed:
            # Pointer-repr Optional[T] (T*) passed to OwnType(OptionalType(T)):
            # generate null-safe conversion instead of unconditional dereference.
            eff = target_type.wrapped if isinstance(target_type, OwnType) else target_type
            expr_type_for_check = self.types.get_resolved_type(expr)
            if (isinstance(target_type, OwnType)
                    and isinstance(eff, OptionalType)
                    and isinstance(expr_type_for_check, OptionalType)
                    and expr_type_for_check.uses_pointer_repr()):
                inner_cpp = expr_type_for_check.inner.to_cpp()
                result = (f"{result} ? std::optional<{inner_cpp}>"
                          f"(std::move(*{result})) : std::nullopt")
            elif (isinstance(eff, OptionalType)
                    and isinstance(expr_type_for_check, OptionalType)
                    and expr_type_for_check.uses_pointer_repr()):
                # Source and target are both the same pointer-repr
                # Optional[T]; pass the pointer through. Hit when the
                # variable is not sema-narrowed (e.g. assigned from None
                # or a function param with no narrowing) -- falling
                # through to the unconditional `(*result)` would deref
                # null. Sema-narrowed locals get is_narrowed=True above
                # and skip this whole block, so the deref there is safe.
                pass
            else:
                result = f"(*{result})"
        # Value optionals are represented as std::optional<T> and must be
        # unwrapped when a concrete value is required.
        expr_type = self.types.get_resolved_type(expr)
        analyzed_type = self.ctx.get_expr_type(expr)
        # Also check the C++ declared type for variables whose sema type was
        # narrowed (e.g. inside `if x is not None:`). The sema type is the
        # narrowed inner type but the C++ variable is still std::optional<T>.
        # Fields always use std::optional<T> regardless of uses_pointer_repr(),
        # so include field accesses when sema has actually narrowed them.
        cpp_declared_type = self._get_cpp_declared_type(expr)
        is_narrowed_optional_field = (
            isinstance(expr, TpyFieldAccess)
            and cpp_declared_type is not None
            and isinstance(cpp_declared_type, OptionalType)
            and analyzed_type is not None
            and not isinstance(analyzed_type, OptionalType)
        )
        is_value_optional = (
            isinstance(expr_type, OptionalType) and not expr_type.uses_pointer_repr()
        ) or is_narrowed_optional_field or (
            cpp_declared_type is not None
            and isinstance(cpp_declared_type, OptionalType)
            and not cpp_declared_type.uses_pointer_repr()
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
        when needed by the target type.

        Complements the `optional_strview_to_str` coercion: that coercion fires
        when the TPy types differ (Optional[StrView] source, Optional[str]
        target). This shim handles the case where TPy types match (both
        Optional[str]) but the C++ representations differ -- `Optional[str]`
        lowers to `std::optional<std::string_view>` at ARG and
        `std::optional<std::string>` elsewhere. The coercion system doesn't
        see the ARG/non-ARG split for same-TPy-type transitions, so a
        dedicated path patches names whose declared param type is an
        Optional[str]."""
        if target_type is None:
            return result
        declared = self.ctx.current_func_params.get(name)
        if not (isinstance(declared, OptionalType)
                and is_str_type(declared.inner)):
            return result
        target = target_type
        if isinstance(target, OwnType):
            target = target.wrapped
        if isinstance(target, OptionalType) and is_str_type(target.inner):
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

    def gen_expr_narrowed(self, expr: TpyExpr) -> tuple[str, TpyType]:
        """Generate expression and type with narrowing applied.

        Returns (cpp_expr, effective_type) where both reflect narrowing state.
        - Optional narrowing: unwraps std::optional<T> to T with (*expr)
        - Union narrowing: uses the narrowed variant type from sema

        Use this instead of separate gen_expr_deref + get_resolved_type when
        the caller needs the expression and type to agree on narrowing (e.g.
        f-string interpolation, any context feeding std::format or similar).
        Callers that intentionally handle optionals (print_optional_val, etc.)
        should use gen_expr_deref directly.
        """
        gen = self.gen_expr_deref(expr)
        base = unwrap_readonly(self.types.get_resolved_type(expr))
        if isinstance(base, OptionalType) and self._is_narrowed_value_optional(expr):
            return f"(*{gen})", base.inner
        if isinstance(expr, TpyName) and expr.name in self.ctx.narrowed_vars:
            analyzed = self.ctx.get_expr_type(expr)
            if analyzed is not None:
                return gen, unwrap_readonly(analyzed)
        return gen, base

    def _is_narrowed_value_optional(self, expr: TpyExpr) -> bool:
        """True if expr is an optional whose sema type was narrowed to non-Optional.

        The C++ storage is still std::optional<T> but sema proved it holds a value
        (e.g. inside `if x is not None:`).
        """
        cpp_type = self._get_cpp_declared_type(expr)
        if (cpp_type is not None
                and isinstance(cpp_type, OptionalType)
                and (isinstance(expr, TpyFieldAccess) or not cpp_type.uses_pointer_repr())):
            analyzed = self.ctx.get_expr_type(expr)
            return analyzed is not None and not isinstance(analyzed, OptionalType)
        return False

    def _maybe_unwrap_narrowed_optional(self, expr_obj: TpyExpr, obj: str, needs_deref: bool) -> str:
        """Unwrap narrowed Optional receivers.

        When sema has proven a std::optional<T> variable holds a value,
        the C++ variable is still optional -- dereference it with (*obj).
        Handles simple names, field accesses, and generator-promoted
        Optional locals (whose rendered `obj` is already the inner
        storage-form Optional after the outer init-tracking deref).
        """
        if needs_deref:
            return obj
        cpp_decl = self._get_cpp_declared_type(expr_obj)
        analyzed = self.ctx.get_expr_type(expr_obj)
        is_comp_var = isinstance(expr_obj, TpyName) and expr_obj.name in self.ctx.comp_local_names
        is_storage_optional = self.ctx.is_storage_form_optional_source(expr_obj)
        if (cpp_decl is not None
                and isinstance(cpp_decl, OptionalType)
                and (isinstance(expr_obj, TpyFieldAccess) or is_comp_var
                     or is_storage_optional
                     or not cpp_decl.uses_pointer_repr())
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
        if isinstance(actual_type, NominalType) and actual_type.is_record:
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
            if self.ctx.is_ptr_variant_union(var_type):
                val_cpp = self.types.type_to_cpp(var_type)
                return f"::tpy::to_value_variant<{val_cpp}>({arg.name})"
        arg_expr = self.gen_expr_deref(arg)
        # Record constructors are prvalues — already an rvalue, no copy needed
        if isinstance(arg, TpyCall) and isinstance(arg.func, TpyName) and self.ctx.analyzer.registry.get_record(arg.func_name):
            return arg_expr
        return f"{arg_type.to_cpp()}({arg_expr})"

    def _gen_copy_iter_expr(self, arg: TpyExpr) -> str:
        """Generate a CopyIter wrapping for element-by-element copy."""
        arg_type = self.ctx.get_expr_type(arg)
        # Unwrap OwnType if present (copy_iter(copy(x)) edge case)
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        elem_type = builtin_modules.get_iterable_element_type(arg_type, registry=self.ctx.analyzer.registry)
        if elem_type is None:
            # Fallback: treat like copy() if we can't determine element type
            return self._gen_copy_expr(arg)
        elem_type = unwrap_ref_type(elem_type)
        elem_cpp = self.types.type_to_cpp(elem_type)
        arg_expr = self.gen_expr(arg)
        return f"::tpy::copy_iter<{elem_cpp}>({arg_expr})"

    def _gen_own_iter_expr(self, arg: TpyExpr) -> str:
        """Generate an OwnIter wrapping for consuming iteration."""
        arg_expr = self.gen_expr(arg)
        return f"::tpy::own_iter(std::move({arg_expr}))"

    def _maybe_gen_special_builtin_call(self, expr: TpyCall | TpyMethodCall) -> str | None:
        """Emit C++ for the four @builtin_function calls with bespoke shapes,
        keyed on the resolved function's qualified_name so import path doesn't
        matter. Returns None if expr doesn't resolve to one of them.
        """
        fi = expr.resolved_function_info
        if fi is None:
            return None
        qname = fi.qualified_name
        if qname == qnames.COPY:
            return self._gen_copy_expr(expr.args[0])
        if qname == qnames.COPY_ITER:
            return self._gen_copy_iter_expr(expr.args[0])
        if qname == qnames.OWN_ITER:
            return self._gen_own_iter_expr(expr.args[0])
        if qname == qnames.TRY_PARSE:
            enum_type = fi.return_type.inner
            cpp_type = enum_type.to_cpp()
            arg = self.gen_expr(expr.args[1])
            return f"::tpy::EnumUtil<{cpp_type}>::try_parse({arg})"
        return None

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

    def _gen_consuming_iter(self, expr: TpyExpr, gen_code: str) -> str | None:
        """If expr is a last-use movable local with a consuming __iter__,
        generate the consuming call. Returns None if not applicable.

        Used by both for-loop codegen and call-site arg generation.
        """
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if not (isinstance(inner, TpyName)
                and inner.name in self.ctx.movable_locals
                and id(inner) in self.ctx.analyzer.ctx.all_last_uses):
            return None
        arg_type = self.ctx.get_expr_type(expr)
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        record = self.ctx.analyzer.registry.get_record_for_type(arg_type)
        if record is None:
            return None
        for fi in record.get_method_overloads("__iter__"):
            if fi.is_consuming:
                if fi.native_name:
                    return f"{qualify_native_name(fi.native_name)}(std::move({gen_code}))"
                else:
                    return f"std::move({gen_code}).__iter__()"
        return None

    def _gen_dynamic_protocol_arg(self, arg: TpyExpr, ptype: TpyType) -> str | None:
        """If ptype is a @dynamic protocol, return the wrapped arg expression. Otherwise None."""
        unwrapped_ptype = unwrap_readonly(ptype)
        if not is_protocol_type(unwrapped_ptype):
            return None
        protocol_info = protocol_info_of(unwrapped_ptype)
        if not protocol_info or not protocol_info.is_dynamic:
            return None
        arg_type = self.ctx.get_expr_type(arg)
        if is_protocol_type(arg_type):
            return self.gen_expr_deref(arg, ptype)
        elif self.protocols.directly_implements_dynamic(arg_type, unwrapped_ptype):
            if self.ctx.is_temporary_expr(arg):
                concrete_cpp = self.types.type_to_cpp(arg_type)
                arg_expr = self.gen_expr(arg, arg_type)
                return self.ctx.temps.create_typed(concrete_cpp, arg_expr, brace_init=True)
            else:
                return self.gen_call_arg(arg, ptype)
        else:
            concrete_cpp = self.types.type_to_cpp(arg_type)
            arg_expr = self.gen_expr_deref(arg, arg_type)
            if self.ctx.is_temporary_expr(arg):
                adapter_type = self.protocols.get_dynamic_adapter_type(unwrapped_ptype, concrete_cpp)
            else:
                adapter_type = self.protocols.get_dynamic_ref_adapter_type(unwrapped_ptype, concrete_cpp)
            return self.ctx.temps.create_typed(adapter_type, arg_expr, brace_init=True)

    def _gen_covariant_arg(self, arg: TpyExpr, ptype: TpyType) -> str | None:
        """If ptype requires covariant conversion, return the wrapped arg. Otherwise None."""
        if not isinstance(ptype, NominalType) or not ptype.is_user_record:
            return None
        arg_type = self.ctx.get_expr_type(arg)
        if not isinstance(arg_type, NominalType) or not arg_type.is_user_record:
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
                     inline_template: bool = False,
                     target_const_borrow: bool = False) -> str:
        """Generate a call argument with auto-move at last use for Own[T] params.

        target_type overrides ptype as the hint passed to gen_expr_deref.
        Pass None explicitly to suppress the target hint (e.g. record method
        args where the resolved param type should only drive the move check,
        not literal coercion).

        inline_template: when True, the callee is a cpp_template expansion
        (e.g. push_back), not a real C++ function with T&& param. The callee
        natively accepts lvalues, so the copy-into-temp + move is unnecessary
        for plain names and literals.

        target_const_borrow: when True, the callee param is deep-const-inferred
        (its slots/inner pointers are const). Storage->pointer conversion
        helpers must produce const-pointer slots so const-source iteration
        flows match the param's expected shape.
        """
        # Bytes literal targeting a span-storage slot: pin to static storage
        # via bytes_literal() so the stored span doesn't borrow a temporary
        # vector. Own[bytes] is excluded -- its storage IS vector (list.append
        # of bytes elements expects vector<uint8_t>, not span).
        bytes_lit_arg = arg
        while isinstance(bytes_lit_arg, TpyCoerce):
            bytes_lit_arg = bytes_lit_arg.expr
        if isinstance(bytes_lit_arg, TpyBytesLiteral) and ptype is not None:
            use_static_span = is_bytes_type(ptype) or is_bytes_view_type(ptype)
            if not use_static_span:
                inner_ptype = unwrap_readonly(unwrap_ref_type(ptype))
                if isinstance(inner_ptype, OwnType):
                    inner_ptype = unwrap_readonly(inner_ptype.wrapped)
                    use_static_span = is_bytes_view_type(inner_ptype)
            if use_static_span:
                if not bytes_lit_arg.value:
                    return "std::span<const uint8_t>{}"
                return cpp_bytes_literal_span(bytes_lit_arg.value)
        gen_arg = self.gen_expr_deref(arg, ptype if target_type is _UNSET else target_type)
        # Storage-form Optional source -> Ptr[T] param: source is
        # `std::optional<T>` (lvalue field/subscript), slot wants T*. Mirror
        # of the existing storage-form-to-borrow-form lift performed for
        # OptionalType destinations in `_gen_optional_ptr_arg`.
        if ptype is not None:
            ptype_inner = unwrap_readonly(unwrap_ref_type(ptype))
            if isinstance(ptype_inner, PtrType):
                arg_type = self.ctx.get_expr_type(arg)
                if (isinstance(arg_type, OptionalType)
                        and arg_type.uses_pointer_repr()
                        and self.ctx.is_storage_form_optional_source(arg)):
                    gen_arg = f"::tpy::optional_to_ptr({gen_arg})"
        # Tuple-of-pointer-repr-Optional param: bridge between borrow form
        # (std::tuple<T*, ...>, bare tuple param) and storage form
        # (std::tuple<std::optional<T>, ...>, Own[tuple] param). Source/slot
        # mismatch on either side needs an element-wise converter.
        if ptype is not None:
            ptype_inner = unwrap_readonly(unwrap_ref_type(ptype))
            slot_is_storage = isinstance(ptype_inner, OwnType)
            if slot_is_storage:
                ptype_inner = unwrap_readonly(unwrap_ref_type(ptype_inner.wrapped))
            if (isinstance(ptype_inner, TupleType)
                    and ptype_inner.has_pointer_repr_optional_element()):
                arg_is_storage = self.ctx.is_storage_form_source(arg)
                if arg_is_storage and not slot_is_storage:
                    ptype_cpp = (ptype_inner.to_cpp_return_const() if target_const_borrow
                                 else ptype_inner.to_cpp_return())
                    gen_arg = f"::tpy::tuple_to_pointer<{ptype_cpp}>({gen_arg})"
                elif not arg_is_storage and slot_is_storage:
                    # Tuple literal source: sema's per-element Own check
                    # cleared every element as movable (last-use lvalue,
                    # fresh rvalue, or copy() rvalue), so the lift moves
                    # each pointee into the destination optional rather
                    # than copying. Other rvalue sources (e.g. function
                    # returns of tuple[T_ref|None,...]) stay on the copy
                    # path -- moving from a returned pointer would alias
                    # storage the caller still owns.
                    helper = ("tuple_to_storage_move"
                              if isinstance(arg, TpyTupleLiteral)
                              else "tuple_to_storage")
                    gen_arg = f"::tpy::{helper}<{ptype_inner.to_cpp()}>({gen_arg})"
        if ptype is not None:
            # Auto-consuming iteration: Iterable[Own[T]] param with last-use arg
            # that has consuming __iter__. Generate consuming call instead of copy.
            ptype_inner = unwrap_readonly(unwrap_ref_type(ptype))
            if (is_protocol_type(ptype_inner) and isinstance(ptype_inner, NominalType)
                    and ptype_inner.name == "Iterable"
                    and ptype_inner.type_args
                    and any(isinstance(a, OwnType) for a in ptype_inner.type_args)):
                consuming = self._gen_consuming_iter(arg, gen_arg)
                if consuming is not None:
                    return consuming
            own = unwrap_optional_own(unwrap_readonly(unwrap_ref_type(ptype)))
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
                                self.types.type_to_cpp(own.wrapped), gen_arg, brace_init=True)
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
        if target_type is not None:
            target_type = unwrap_ref_type(target_type)
        if isinstance(expr, TpyIntLiteral):
            return self._gen_int_literal_value(expr.value, target_type)

        elif isinstance(expr, TpyFloatLiteral):
            val = repr(expr.value)
            if is_float32_type(target_type):
                if val == "inf":
                    return "std::numeric_limits<float>::infinity()"
                if val == "-inf":
                    return "(-std::numeric_limits<float>::infinity())"
                if val == "nan":
                    return "std::numeric_limits<float>::quiet_NaN()"
                return val + "f"
            if val == "inf":
                return "std::numeric_limits<double>::infinity()"
            if val == "-inf":
                return "(-std::numeric_limits<double>::infinity())"
            if val == "nan":
                return "std::numeric_limits<double>::quiet_NaN()"
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
            if isinstance(coerce_target, OptionalType) and is_span(coerce_target.inner):
                coerce_target = coerce_target.inner
            # Forward coercion target to literals/views so they can pick the
            # right C++ representation (e.g. bytes_literal vs vector<uint8_t>,
            # char literal vs string)
            if isinstance(expr.expr, (TpyStrLiteral, TpyBytesLiteral)) or is_span(coerce_target) or expr.coercion.name in ("int_literal_to_fixed_int", "float_literal_to_float32"):
                inner_target = coerce_target
            else:
                inner_target = expr.actual_type
            inner_expr = expr.expr
            # INTO_ANY copies internally via std::any -- an explicit copy()
            # wrapper at the source is redundant. Sema kept the call for the
            # warning-silencing semantics; codegen unwraps so we don't emit
            # T(x) on top of make_any's already-copying construction. Skip
            # the unwrap for ptr-variant unions: copy() there generates
            # to_value_variant(...), which converts representation; dropping
            # it would store the ptr-variant directly (dangling pointers).
            if expr.coercion.name == "into_any":
                candidate = self.ctx.unwrap_copy(inner_expr)
                if candidate is not inner_expr:
                    cand_type = self.ctx.get_expr_type(candidate)
                    if not (cand_type is not None
                            and self.ctx.is_ptr_variant_union(cand_type)):
                        inner_expr = candidate
            gen_inner = self.gen_expr(inner_expr, inner_target)
            if is_span(coerce_target):
                return self._gen_span_coercion(expr.expr, coerce_target, gen_inner)
            # IntLiteralType may be runtime BigInt; sema records this on the coercion.
            if expr.coercion.name == "int_literal_to_fixed_int":
                return gen_inner
            # Coercions that call methods on the inner expression need dereferencing for globals
            if expr.coercion.name in ("record_to_ptr", "record_to_const_ptr",
                                      "upcast_to_ptr", "upcast_to_const_ptr",
                                      "bigint_to_fixed_int", "value_to_ptr"):
                if self.ctx.is_indirect_name(expr.expr):
                    gen_inner = f"(*{gen_inner})"
            return expr.coercion.codegen(gen_inner, expr.actual_type, expr.expected_type, expr.context_kind)

        elif isinstance(expr, TpyStrLiteral):
            # If target type is Char and single char, output as char literal
            if is_char_type(target_type) and len(expr.value) == 1:
                return f"'{escape_cpp_char(expr.value)}'"
            return cpp_string_literal_expr(expr.value)

        elif isinstance(expr, TpyBytesLiteral):
            if not expr.value:
                if is_bytes_view_type(target_type):
                    return "std::span<const uint8_t>{}"
                return "std::vector<uint8_t>{}"
            if is_bytes_view_type(target_type):
                return cpp_bytes_literal_span(expr.value)
            hex_bytes = ", ".join(f"0x{b:02x}" for b in expr.value)
            return f"std::vector<uint8_t>{{{hex_bytes}}}"

        elif isinstance(expr, TpyName):
            # Function reference: generate qualified C++ function name
            if expr.is_function_ref and expr.function_ref_info is not None:
                return self._gen_function_ref(expr)
            # Union type narrowing: use the std::get-extracted local
            if expr.name in self.ctx.narrowed_vars:
                return self.ctx.narrowed_vars[expr.name]
            # In generator method __next__(), self -> __self (struct reference field)
            if expr.name == "self" and self.ctx.generator_self_ref is not None:
                return self.ctx.generator_self_ref
            # self -> this (pointer) in instance methods; callers use
            # is_indirect_name to decide -> vs . and (*x) for value deref
            if expr.name == "self" and self.ctx.in_method and "self" not in self.ctx.current_func_params:
                return "this"
            # Native global name substitution (Python name -> C/C++ name)
            # Skip if shadowed by a local variable
            if expr.name in self.ctx.native_global_names and expr.name not in self.ctx.local_scope_names:
                return qualify_native_name(self.ctx.native_global_names[expr.name])
            # Imported variable from a user module. Read through
            # `imported_names` (not the attribute table) because uses
            # that source-precede a later top-level redefine still
            # need to qualify to the import; `imported_names` is the
            # shadow-resilient history tracker. Filter to variables by
            # checking the immediate source's `variables` dict.
            imp = self.ctx.analyzer.imported_names.get(expr.name)
            if imp is not None:
                src_mod_imm, orig_imm = imp
                src_info_imm = self.ctx.analyzer.registry.get_module(src_mod_imm)
                if src_info_imm is not None and orig_imm in src_info_imm.variables:
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
                    # Use qualified import reference (convert dotted name
                    # to C++ namespace). Pointer indirection for non-value
                    # globals is handled by is_indirect_name() ->
                    # gen_expr_deref() at call sites. Follow the
                    # re-export chain to the ultimate defining module so
                    # the qname renders against a module that actually
                    # emits the symbol (matters for facade re-exports).
                    source_module, original_name = resolve_definer(
                        self.ctx.analyzer.registry,
                        src_mod_imm, orig_imm, SymbolKind.VARIABLE)
                    source_info = self.ctx.analyzer.registry.get_module(source_module)
                    # native_global variables use a user-specified C++ symbol name
                    # (e.g. "engine::score") that's independent of the module's
                    # cpp_namespace -- look it up in the source module's ModuleInfo.
                    if source_info is not None:
                        var_info = source_info.variables.get(original_name)
                        if var_info is not None and var_info.native_cpp_name is not None:
                            return var_info.native_cpp_name
                    return qualified_cpp_name(source_module, original_name)
            result = escape_cpp_name(expr.name)
            # Generator body: optional-wrapped fields need dereference
            if self.ctx.in_generator_body and expr.name in self.ctx.generator_optional_fields:
                result = f"(*{result})"
            return self._maybe_convert_opt_str_param(expr.name, result, target_type)

        elif isinstance(expr, TpyBinOp):
            return self._gen_binop(expr, target_type)

        elif isinstance(expr, TpyChainedCompare):
            return self._gen_chained_compare(expr)

        elif isinstance(expr, TpyUnaryOp):
            return self._gen_unaryop(expr, target_type)

        elif isinstance(expr, TpyCall):
            if expr.macro_expansion is not None:
                return self.gen_expr(expr.macro_expansion, target_type)
            if expr.dyn_hasattr_call is not None:
                return self._gen_dyn_hasattr_block(expr.dyn_hasattr_call)
            if expr.dyn_getattr_default_call is not None:
                return self._gen_dyn_getattr_default_block(
                    expr.dyn_getattr_default_call, expr.args[2])
            return self._post_process_call(expr, self._gen_call(expr))

        elif isinstance(expr, TpyMethodCall):
            if expr.fstr_expansion is not None:
                return self.gen_expr(expr.fstr_expansion)
            if expr.macro_expansion is not None:
                return self.gen_expr(expr.macro_expansion, target_type)
            return self._post_process_call(expr, self._gen_method_call(expr))

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
        if is_int_enum_type(var_type):
            cpp_underlying = enum_info_of(var_type).underlying_type.to_cpp()
            return f"(static_cast<{cpp_underlying}>({rendered}) != 0)"
        if is_enum_type(var_type):
            return "true"
        if isinstance(var_type, AnyType):
            return f"::tpy::to_bool({rendered})"
        if isinstance(var_type, OptionalType) and not var_type.uses_pointer_repr():
            return f"::tpy::is_truthy({rendered})"
        if is_any_str_type(var_type):
            return f"(!{rendered}.empty())"
        if is_any_bytes_type(var_type):
            return f"(!{rendered}.empty())"
        # Primitive types with implicit C++ bool conversion -- skip __bool__ dispatch.
        if (is_bool_type(var_type) or is_fixed_int_type(var_type) or is_big_int_type(var_type)
                or is_float64_type(var_type) or is_float32_type(var_type)
                or is_char_type(var_type) or isinstance(var_type, IntLiteralType)):
            return rendered
        record = self.ctx.analyzer.registry.get_record_for_type(var_type)
        if record:
            if record.get_method_overloads("__bool__"):
                return f"::tpy::__bool__({rendered})"
            if record.get_method_overloads("__len__"):
                return f"(::tpy::__len__({rendered}) != 0)"
            # User records without __bool__/__len__ are always truthy (Python default).
            if isinstance(var_type, NominalType) and var_type.is_user_record:
                return "true"
        # Implicit bool conversion (ptr, etc.)
        return rendered

    def _is_str_view_at_runtime(self, expr: TpyExpr) -> bool:
        """True if this expression produces std::string_view at C++ runtime.

        Cases that produce string_view despite being typed as str in sema:
        - str params: C++ signature uses string_view (via to_cpp_param)
        - str literals: materialized as std::string_view temps in _gen_logical_value;
          treated as string_view here so recursive chain detection works correctly.
          Callers in _gen_if_expr must guard separately: literals are const char*
          there and need no explicit wrapping (C++ handles string?const-char* natively).
        - StrView locals: explicitly typed as string_view
        Nested and/or/ternary chains are handled recursively.
        """
        if isinstance(expr, TpyName):
            param_type = self.ctx.current_func_params.get(expr.name)
            return (is_str_type(param_type)
                    or (isinstance(param_type, LiteralType) and param_type.is_str_base())
                    or is_str_view_type(self.types.get_resolved_type(expr)))
        if isinstance(expr, TpyStrLiteral):
            return True
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            return (self._is_str_view_at_runtime(expr.left)
                    and self._is_str_view_at_runtime(expr.right))
        if isinstance(expr, TpyIfExpr):
            return (self._is_str_view_at_runtime(expr.then_expr)
                    and self._is_str_view_at_runtime(expr.else_expr))
        return is_str_view_type(self.types.get_resolved_type(expr))

    def _is_bytes_view_at_runtime(self, expr: TpyExpr) -> bool:
        """True if this expression produces std::span<const uint8_t> at C++ runtime.

        bytes params use span via to_cpp_param. Bytes literals with BytesView
        resolved type use static storage. BytesView locals are explicit views.
        """
        if isinstance(expr, TpyBytesLiteral):
            return is_bytes_view_type(self.types.get_resolved_type(expr))
        if isinstance(expr, TpyName):
            return (is_bytes_type(self.ctx.current_func_params.get(expr.name))
                    or is_bytes_view_type(self.types.get_resolved_type(expr)))
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            return (self._is_bytes_view_at_runtime(expr.left)
                    and self._is_bytes_view_at_runtime(expr.right))
        if isinstance(expr, TpyIfExpr):
            return (self._is_bytes_view_at_runtime(expr.then_expr)
                    and self._is_bytes_view_at_runtime(expr.else_expr))
        return is_bytes_view_type(self.types.get_resolved_type(expr))


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
            if is_list(lhs_type) and isinstance(expr.left, TpyArrayLiteral):
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
            if is_list(rhs_type) and isinstance(expr.right, TpyArrayLiteral):
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
            bigint_target = resolved if is_big_int_type(resolved) else None
            return self._gen_int_literal_value(analyzed_type.value, bigint_target)

        # First pass: get raw types to detect fixed-int operands
        left_raw = self.types.get_resolved_type(expr.left)
        right_raw = self.types.get_resolved_type(expr.right)
        # If one operand is a fixed-width int, resolve literals as that type (not BigInt)
        fixed_context = target_type if is_fixed_int_type(target_type) else None
        if is_fixed_int_type(left_raw):
            fixed_context = left_raw
        elif is_fixed_int_type(right_raw):
            fixed_context = right_raw
        left_type = self.types.get_resolved_type(expr.left, fixed_context)
        right_type = self.types.get_resolved_type(expr.right, fixed_context)

        # Handle 'in' and 'not in' operators
        if expr.op in ("in", "not in"):
            # TypedDict: "key" in td -> compile-time field presence check
            if expr.typed_dict_in_field is not None:
                negate = expr.op == "not in"
                if expr.typed_dict_in_always_true:
                    return "false" if negate else "true"
                right = self.gen_expr(expr.right)
                if self.ctx.is_indirect_name(expr.right):
                    right = f"(*{right})"
                cpp_field = escape_cpp_name(expr.typed_dict_in_field)
                has_value = f"{right}.{cpp_field}.has_value()"
                return f"(!{has_value})" if negate else has_value
            if self.ctx.literal_facts:
                folded = self._try_fold_literal_in(expr)
                if folded is not None:
                    return folded
            # Tuple literal membership: x in (1, 2, 3) -> (x == 1 || x == 2 || x == 3)
            if isinstance(expr.right, TpyTupleLiteral):
                left = self.gen_expr(expr.left)
                elems = [self.gen_expr(e) for e in expr.right.elements]
                # For multi-element tuples with non-trivial LHS, bind LHS to a
                # temp to avoid evaluating it multiple times (side effects).
                need_temp = (len(elems) > 1
                             and not isinstance(expr.left, (TpyName, TpyIntLiteral,
                                                            TpyFloatLiteral, TpyStrLiteral,
                                                            TpyBoolLiteral)))
                if need_temp:
                    conditions = [f"(__in_lhs == {e})" for e in elems]
                    joined = " || ".join(conditions)
                    negate = expr.op == "not in"
                    body = f"!({joined})" if negate else joined
                    return f"({{ auto&& __in_lhs = {left}; {body}; }})"
                conditions = [f"({left} == {e})" for e in elems]
                joined = " || ".join(conditions) if conditions else "false"
                if expr.op == "not in":
                    return f"(!({joined}))" if len(conditions) > 1 else f"(!{conditions[0]})"
                return f"({joined})"
            right_type_for_left = self.types.get_resolved_type(expr.right)
            left = self.gen_expr(expr.left, view_key_target(right_type_for_left))
            right = self.gen_expr(expr.right)
            # Dereference globals for .begin()/.end() calls
            if self.ctx.is_indirect_name(expr.right):
                right = f"(*{right})"
            negate = expr.op == "not in"
            if expr.resolved_contains:
                find_expr = f"({self.builtins.gen_call_from_fi(expr.resolved_contains, right, [left])})"
                return f"(!{find_expr})" if negate else find_expr
            elif is_any_str_type(self.types.get_resolved_type(expr.right)):
                # String contains: use .find(). Wrap string literals in
                # std::string_view since C string literals lack .find().
                rhs = f"std::string_view({right})" if isinstance(expr.right, TpyStrLiteral) else right
                op = "==" if negate else "!="
                return f"({rhs}.find({left}) {op} std::string::npos)"
            else:
                # Collection `in` fallback (no __contains__).
                right_type = self.types.get_resolved_type(expr.right)
                record = self.ctx.analyzer.registry.get_record_for_type(right_type)
                # Built-in NativeIterable or NativeIterable[T] protocol param:
                # efficient std::find with real begin/end.
                is_native_in = (
                    (record is not None and record.is_native
                     and builtin_modules.is_native_iterable(right_type, registry=self.ctx.analyzer.registry))
                    or (is_protocol_type(right_type)
                        and right_type.qualified_name() == "tpy.NativeIterable")
                )
                if is_native_in:
                    neg = "!" if negate else ""
                    return f"{neg}std::ranges::contains({right}, {left})"
                # Universal path: __iter__ + __next__ via statement expression.
                # unwrap_ref handles val_or_ref from native_iterator's __next__
                neg = "!" if negate else ""
                return (f"{neg}({{ auto&& __itr = ::tpy::__iter__({right}); "
                        f"bool __found = false; "
                        f"for (;;) {{ auto __r = __itr.__next__(); "
                        f"if (!__r.has_value()) break; "
                        f"if (::tpy::unwrap_ref(*__r) == {left}) {{ __found = true; break; }} }} "
                        f"__found; }})")

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
                # @overload context: the specialization narrowed this Optional
                # param to a concrete type (its inner T or None). Fold the test
                # so dead-branch elim can strip the losing branch.
                if (isinstance(opt_expr, TpyName)
                        and opt_expr.name in self.ctx.overload_param_types):
                    concrete = self.ctx.overload_param_types[opt_expr.name]
                    is_none = isinstance(concrete, NoneType)
                    if expr.op == "is":
                        return "true" if is_none else "false"
                    else:
                        return "false" if is_none else "true"
                # Static protocol params (single optional or union with None)
                # always use pointer comparison
                opt_type = left_type if opt_expr is expr.left else right_type
                if self.protocols.is_static_protocol_param(opt_type):
                    val = self.gen_expr(opt_expr)
                    cpp_op = "==" if expr.op == "is" else "!="
                    return f"({val} {cpp_op} nullptr)"
                # Indirect names (T* pointer-locals/globals) use pointer comparison.
                # OPTIONAL_STORAGE names are also indirect but compare via
                # .has_value() -- nullptr comparison isn't defined for
                # std::optional<T>.
                if self.ctx.is_indirect_name(opt_expr):
                    val = self.gen_expr(opt_expr)
                    if (isinstance(opt_expr, TpyName)
                            and self.ctx.needs_optional_to_ptr_lift(opt_expr.name)):
                        if expr.op == "is":
                            return f"(!{val}.has_value())"
                        return f"({val}.has_value())"
                    cpp_op = "==" if expr.op == "is" else "!="
                    return f"({val} {cpp_op} nullptr)"
                # Pointer-repr Optional sources rendered as borrow-form `T*`
                # (function/method call returns, etc.) use nullptr comparison;
                # storage-form sources (field/subscript) use .has_value().
                val = self.gen_expr(opt_expr)
                if (opt_type.uses_pointer_repr()
                        and not self.ctx.is_storage_form_optional_source(opt_expr)):
                    cpp_op = "==" if expr.op == "is" else "!="
                    return f"({val} {cpp_op} nullptr)"
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
                # Recursive union wrapper: access .data for variant operations
                union_type = left_type if isinstance(left_type, UnionType) else right_type
                val = self.ctx.variant_data_expr(val, union_type)
                check = f"std::holds_alternative<std::monostate>({val})"
                if expr.op == "is":
                    return f"({check})"
                else:
                    return f"(!{check})"
            # Any vs None: typeid-based check (D15). The Any cell stores
            # None as a value of type std::nullptr_t; an empty/moved-from
            # Any is *not* None (it has no value at all).
            any_expr = None
            if isinstance(left_type, AnyType) and isinstance(expr.right, TpyNoneLiteral):
                any_expr = expr.left
            elif isinstance(right_type, AnyType) and isinstance(expr.left, TpyNoneLiteral):
                any_expr = expr.right
            if any_expr is not None:
                val = self.gen_expr(any_expr)
                check = (f"({val}.value.has_value() && "
                         f"{val}.value.type() == typeid(std::nullptr_t))")
                if expr.op == "is":
                    return check
                return f"(!{check})"
            # Fallback: pointer comparison
            cpp_op = "==" if expr.op == "is" else "!="
            left = self.gen_expr(expr.left)
            right = self.gen_expr(expr.right)
            return f"({left} {cpp_op} {right})"

        # Logical operators
        if expr.op in ("&&", "||"):
            if self.ctx.literal_facts:
                folded = self._try_fold_literal_chain(expr)
                if folded is not None:
                    return folded
            result_type = self.types.get_resolved_type(expr)
            if not is_bool_type(result_type):
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
            # Dead branch elimination: fold comparisons on single-value LiteralType
            if expr.op in ("==", "!="):
                folded = self._try_fold_literal_comparison(expr)
                if folded is not None:
                    return folded
            left_target, right_target = self._comparison_targets(expr)
            # Mixed-sign fixed-int comparison: use std::cmp_* so the result is
            # mathematically correct without the "promote signed to unsigned"
            # reinterpretation that -Wsign-compare warns about. Done before
            # operand emission so we sidestep target/coercion adjustments that
            # would force a same-sign cast (and therefore the bug we're avoiding).
            #
            # Skip when either operand's analyzer-level type is IntLiteralType:
            # the literal coerces cleanly to the typed side (`x > 0` etc.),
            # GCC doesn't trip -Wsign-compare on literal-vs-typed comparisons
            # for in-range values, and emitting cmp_* there is just noise.
            # (Sema's gate has an additional `literal_default_vars` check for
            # locals still pending retro-widen; codegen runs after retro-widen
            # so those locals already have their final type here.)
            left_lit = isinstance(self.ctx.get_expr_type(expr.left), IntLiteralType)
            right_lit = isinstance(self.ctx.get_expr_type(expr.right), IntLiteralType)
            if (left_target is None and right_target is None
                    and not left_lit and not right_lit
                    and _mixed_sign_fixed_int(left_raw, right_raw)):
                left = self.gen_expr_deref(expr.left)
                right = self.gen_expr_deref(expr.right)
                return f"{_CMP_HELPER[expr.op]}({left}, {right})"
            left = self.gen_expr_deref(expr.left, left_target)
            right = self.gen_expr_deref(expr.right, right_target)

            # BigInt has no implicit conversion to/from double in C++, so
            # mixed BigInt/float comparisons need an explicit cast (mirroring
            # Python's int-to-float promotion for comparisons).
            left_cmp = left_target if left_target is not None else left_type
            right_cmp = right_target if right_target is not None else right_type
            if is_big_int_type(left_cmp) and is_float_type(right_cmp):
                left = f"static_cast<{right_cmp.to_cpp()}>({left})"
            elif is_big_int_type(right_cmp) and is_float_type(left_cmp):
                right = f"static_cast<{left_cmp.to_cpp()}>({right})"

            # IntEnum coercion: cast enum operand(s) to underlying type.
            # int_enum_coercion is set by sema only for types that passed
            # is_int_enum_type, which means they flowed through register_enum
            # and have a populated TypeDef.enum payload -- enum_info_of is
            # guaranteed non-None here.
            if expr.int_enum_coercion:
                einfo = enum_info_of(expr.int_enum_coercion)
                assert einfo is not None, "int_enum_coercion set on a type without registered EnumInfo"
                underlying_cpp = self.types.type_to_cpp(einfo.underlying_type)
                if is_int_enum_type(left_type):
                    left = f"static_cast<{underlying_cpp}>({left})"
                if is_int_enum_type(right_type):
                    right = f"static_cast<{underlying_cpp}>({right})"
            # Use sema-resolved comparison method when available.
            if expr.resolved_binop:
                result = self._gen_binop_from_result(expr.resolved_binop, left, right)
                # != resolved via __eq__ -> negate
                if expr.op == "!=" and expr.resolved_binop.method.name == "__eq__":
                    return f"(!({result}))"
                return f"({result})"
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
        if (is_fixed_int_type(target_type) and left_is_literal and right_is_literal):
            # Pass target_type to handle nested binops like 1 + (2 + 3)
            left = self.gen_expr(expr.left, target_type)
            right = self.gen_expr(expr.right, target_type)
            # Use registry to get the fixed-int binary operator
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                fi = self.builtins.get_type_method_fi(target_type, method_name)
                if fi:
                    return self.builtins.gen_call_from_fi(fi, left, [right])
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
                underlying_cpp = self.types.type_to_cpp(enum_info_of(expr.int_enum_coercion).underlying_type)
                if is_int_enum_type(left_type):
                    left = f"static_cast<{underlying_cpp}>({left})"
                if is_int_enum_type(right_type):
                    right = f"static_cast<{underlying_cpp}>({right})"
            # C++ can't deduce template params from bare initializer lists,
            # so array literal operands need explicit std::vector<T>{...} prefix
            if is_list(receiver_type):
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
        if isinstance(left_type, NominalType) and left_type.is_record:
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
        if is_big_int_type(left_cmp) and is_float_type(right_cmp):
            left_str = f"static_cast<{right_cmp.to_cpp()}>({left_str})"
        elif is_big_int_type(right_cmp) and is_float_type(left_cmp):
            right_str = f"static_cast<{left_cmp.to_cpp()}>({right_str})"

        # IntEnum coercion
        if pair.int_enum_coercion:
            underlying_cpp = self.types.type_to_cpp(enum_info_of(pair.int_enum_coercion).underlying_type)
            if is_int_enum_type(left_type):
                left_str = f"static_cast<{underlying_cpp}>({left_str})"
            if is_int_enum_type(right_type):
                right_str = f"static_cast<{underlying_cpp}>({right_str})"

        return f"({left_str} {pair.op} {right_str})"

    @staticmethod
    def _is_simple_expr(expr: TpyExpr) -> bool:
        """Check if an expression is side-effect-free (safe to duplicate).

        TODO: replace with an `is_pure` bit computed during sema and attached
        to THIR nodes (see docs/IR_DESIGN.md). The current syntactic check
        misses cases like `Int32(0)` -- a primitive-type constructor call
        that codegen elides to a bare literal -- so chained-compare endpoint
        inlining still binds a temp for it.
        """
        if isinstance(expr, TpyCoerce):
            return ExpressionGenerator._is_simple_expr(expr.expr)
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

    def _try_fold_literal_comparison(self, expr: TpyBinOp) -> str | None:
        """Fold == / != when a variable has a known LiteralType.

        Single-value: fold to true/false. Multi-value: fold to false/true
        only when the compared value is NOT in the set.
        """
        for var_side, lit_side in [(expr.left, expr.right), (expr.right, expr.left)]:
            if not isinstance(var_side, TpyName):
                continue
            lit_type = self.ctx.literal_facts.get(var_side.name)
            if not isinstance(lit_type, LiteralType):
                continue
            lit_val = self._extract_literal_value(lit_side)
            if lit_val is None:
                continue
            in_set = lit_val in lit_type.values
            if len(lit_type.values) == 1:
                result = in_set if expr.op == "==" else not in_set
                return "true" if result else "false"
            if not in_set:
                return "false" if expr.op == "==" else "true"
        return None

    # Keep static method alias for backward compatibility (used by statements.py)
    _extract_literal_value = staticmethod(_extract_literal_value)

    def _try_fold_literal_chain(self, expr: TpyBinOp) -> str | None:
        """Fold &&/|| chains to "true"/"false" using literal_facts."""
        left = self._try_fold_literal_operand(expr.left)
        right = self._try_fold_literal_operand(expr.right)
        if expr.op == "||":
            if left is True or right is True:
                return "true"
            if left is False and right is False:
                return "false"
        else:
            if left is False or right is False:
                return "false"
            if left is True and right is True:
                return "true"
        if left is None and right is None:
            result = _check_literal_chain(expr, self.ctx.literal_facts)
            if result is True:
                return "true"
            if result is False:
                return "false"
        return None

    def _try_fold_literal_operand(self, expr: TpyExpr) -> bool | None:
        """Resolve a single operand in a &&/|| chain to a bool."""
        if isinstance(expr, TpyBinOp):
            if expr.op in ("&&", "||"):
                result = self._try_fold_literal_chain(expr)
                return {"true": True, "false": False}.get(result)  # type: ignore[arg-type]
            if expr.op in ("==", "!="):
                result = self._try_fold_literal_comparison(expr)
                return {"true": True, "false": False}.get(result)  # type: ignore[arg-type]
            if expr.op in ("in", "not in"):
                result = self._try_fold_literal_in(expr)
                return {"true": True, "false": False}.get(result)  # type: ignore[arg-type]
        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            inner = self._try_fold_literal_operand(expr.operand)
            if inner is not None:
                return not inner
        return None

    def _try_fold_literal_in(self, expr: TpyBinOp) -> str | None:
        """Fold `x in (a, b, ...)` / `x not in (a, b, ...)` using literal_facts."""
        result = _check_literal_in(expr, self.ctx.literal_facts)
        if result is True:
            return "true"
        if result is False:
            return "false"
        return None

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
        if left_target is None and is_char_type(right_type):
            if not (pair.optional_safe_eq and isinstance(left_type, OptionalType)):
                left_target = CHAR
        if right_target is None and is_char_type(left_type):
            if not (pair.optional_safe_eq and isinstance(right_type, OptionalType)):
                right_target = CHAR

        return left_target, right_target

    def _gen_chained_compare_lambda(self, expr: TpyChainedCompare) -> str:
        """Complex path: GCC stmt-expr with temp vars for single evaluation.

        Builds a right-nested ``cmp && ({ bind_next; next_cmp && (...); })``
        chain so operand evaluation interleaves with comparison: operands
        after a failed comparison are never evaluated. GCC stmt-expr has no
        ``return``, so the block's last expression supplies the value.

        Intermediate operands are always bound to ``_cmpI`` temps (they appear
        in two pairs). Endpoints are bound only when binding is required:
        - First operand: bound iff non-simple. Inlining a non-simple first
          operand would reorder side effects relative to ``_cmp1 = op1``.
        - Last operand: always inlined. It appears only in the innermost
          compare, after every earlier binding has run; reordering vs the
          bound ``_cmp{n-1}`` (pure variable read) is unobservable.
        """
        assert expr.pairs is not None
        all_operands = [expr.left] + expr.comparators
        n = len(expr.pairs)
        # Chained compares have n >= 2 (n == 1 routes to _gen_chained_compare_inline).
        assert n >= 2

        def operand_code(i: int) -> str:
            if i == 0:
                l_tgt, _ = self._comparison_targets(expr.pairs[0])
                return self.gen_expr_deref(all_operands[0], l_tgt)
            _, r_tgt = self._comparison_targets(expr.pairs[i - 1])
            return self.gen_expr_deref(all_operands[i], r_tgt)

        reprs: list[str] = []
        bindings: list[str | None] = []
        for i in range(n + 1):
            code = operand_code(i)
            if 0 < i < n:
                must_bind = True
            elif i == 0:
                must_bind = not self._is_simple_expr(all_operands[0])
            else:
                must_bind = False  # last operand always inlined
            if must_bind:
                reprs.append(f"_cmp{i}")
                bindings.append(f"auto&& _cmp{i} = {code};")
            else:
                reprs.append(code)
                bindings.append(None)

        # Innermost compare (last operand inlined, no wrapper needed).
        inner = self._gen_comparison_pair(expr.pairs[-1], reprs[n - 1], reprs[n])

        # Wrap pairs i = n-2 .. 1: bind intermediate op i+1 + cmp && next.
        for i in range(n - 2, 0, -1):
            cmp_str = self._gen_comparison_pair(expr.pairs[i], reprs[i], reprs[i + 1])
            inner = f"({{ {bindings[i + 1]} {cmp_str} && {inner}; }})"

        # Outer: ops 0 and 1 bindings (if any) + first compare && inner.
        first_cmp = self._gen_comparison_pair(expr.pairs[0], reprs[0], reprs[1])
        outer_parts = [b for b in bindings[:2] if b]
        outer_parts.append(f"{first_cmp} && {inner};")
        return f"({{ {' '.join(outer_parts)} }})"

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
            return self.builtins.gen_call_from_fi(unaryop_result.method, operand, [])

        # IntEnum: unary negation via static_cast
        if is_int_enum_type(operand_type) and expr.op == "-":
            underlying_cpp = self.types.type_to_cpp(enum_info_of(operand_type).underlying_type)
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
            # Any narrowing (D15): the source is a tpy::Any cell, not a
            # variant. The inline narrowed expression must be a
            # std::any_cast borrow, mirroring the statement-level
            # _emit_isinstance_extractions path.
            if isinstance(self.ctx.lookup_var_type(var_name), AnyType):
                result[var_name] = (
                    f"std::any_cast<const {cpp_type}&>({var_ref}.value)"
                )
                continue
            # Pointer-variant unions: *std::get<T*>(var) or *std::get<const T*>(var)
            if var_name in self.ctx.ptr_variant_locals:
                const_pfx = "const " if var_name in self.ctx.const_indirect_locals else ""
                result[var_name] = f"(*std::get<{const_pfx}{cpp_type}*>({var_ref}))"
            else:
                # Recursive union wrapper: access .data for variant operations.
                # Skip if already narrowed (narrowed var is concrete, not a wrapper).
                if var_name not in self.ctx.narrowed_vars:
                    var_decl_type = self.ctx.var_types.get(var_name)
                    get_ref = self.ctx.variant_data_expr(var_ref, var_decl_type) if var_decl_type else var_ref
                else:
                    get_ref = var_ref
                result[var_name] = f"std::get<{cpp_type}>({get_ref})"
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
                # False branch: compute remaining union members.
                # Tuple form stores check types as a UnionType.
                check_type = expr.isinstance_type
                check_members = (set(check_type.members)
                                 if isinstance(check_type, UnionType)
                                 else {check_type})
                var_type = self.ctx.get_expr_type(expr.args[0])
                if isinstance(var_type, UnionType):
                    remaining = [m for m in var_type.members if m not in check_members]
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
        if is_big_int_type(target_type):
            # INT32_MIN is excluded from the bare-literal range: the C++
            # parser sees `-2147483648` as `-(2147483648)`, and the positive
            # `2147483648` exceeds INT_MAX so its type is `long` -- which is
            # distinct from `int64_t` on macOS arm64 (`int64_t == long long`)
            # and triggers an ambiguous-overload error against BigInt's
            # int32_t / int64_t / uint64_t constructors. Anything outside
            # [-INT_MAX, INT_MAX] falls through to the explicit int64_t cast.
            if -(2**31 - 1) <= v <= 2**31 - 1:
                return f"::tpy::BigInt({v})"
            if -2**63 <= v <= 2**63 - 1:
                return f"::tpy::BigInt(static_cast<int64_t>({v}LL))"
            return f'::tpy::BigInt::from_str("{v}")'
        # Wide values need explicit suffix so the C++ parser accepts them
        # without auto-promoting to unsigned. INT64_MIN can't be written as
        # `-N` (parsed as unary-minus over an out-of-range positive); use
        # the (-INT64_MAX - 1) idiom.
        if v > 0x7FFFFFFFFFFFFFFF:
            bare = f"{v}ull"
        elif v == -0x8000000000000000:
            bare = "(-9223372036854775807LL - 1)"
        else:
            bare = str(v)
        # GCC exempts compile-time integer constants of natural type `int`
        # that fit the target from -Wsign-conversion / -Wconversion. Cast
        # only when neither holds, and only for non-default fixed-int
        # targets (the default-int case has bare literal `int` == int32_t
        # so the implicit conversion is identity).
        if target_type is None:
            return bare
        if not is_fixed_int_type(target_type):
            return bare
        if target_type is self.ctx.analyzer.ctx.default_int_type:
            return bare
        if -2**31 <= v <= 2**31 - 1 and fixed_int_range_contains(target_type, v):
            return bare
        cpp_type = self.types.type_to_cpp(target_type)
        return f"static_cast<{cpp_type}>({bare})"

    def _post_process_call(self, expr: TpyCall | TpyMethodCall, call_cpp: str) -> str:
        """Apply the standard post-processing chain to a freshly-generated
        call expression. Keeps the four call-emission sites (gen_expr's
        TpyCall/TpyMethodCall + property-getter and dyn-getattr in
        _gen_field_access) in lockstep when post-steps are added.
        """
        result = self._maybe_error_return_unwrap(expr, call_cpp)
        return self._maybe_native_return_cast(expr, result)

    def _maybe_native_return_cast(self, expr: TpyCall | TpyMethodCall, call_cpp: str) -> str:
        """Wrap a call in static_cast when @native declared cpp_return_type.

        Triggered only when the user explicitly annotated `@native(...,
        cpp_return_type=T)`, signaling the C++ side returns a wider/different
        type than the declared TPy return. Wraps `call_cpp` in
        `static_cast<DECLARED_TPY_RETURN>(...)` so the implicit conversion at
        the use site doesn't trip -Wsign-conversion / -Wconversion. Bare
        @native (no cpp_return_type) keeps exact-match-to-C++ semantics --
        no implicit cast.
        """
        fi = expr.resolved_function_info
        if (fi is not None
                and fi.native_cpp_return_type is not None
                and fi.error_return_type is None
                and fi.return_type is not None):
            cpp_type = self.types.type_to_cpp(fi.return_type)
            return f"static_cast<{cpp_type}>({call_cpp})"
        return call_cpp

    def _maybe_error_return_unwrap(self, expr: TpyCall | TpyMethodCall, call_cpp: str) -> str:
        """Wrap an @error_return call in a statement expression that unwraps it.

        Uses GCC/Clang statement expressions: ({ auto __t = call(); check; *__t; })
        The check depends on context:
        - Inside @error_return function: propagate via return
        - Inside try/except: goto except label
        - Top-level: panic
        """
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        fi = getattr(inner, 'resolved_function_info', None)
        if not fi or not fi.error_return_type:
            return call_cpp

        # Skip if statement-level handlers will take care of this call.
        # The flag is set for the direct (top-level) call only -- clear it
        # so nested calls in arguments still get unwrapped.
        if self.ctx.error_return_stmt_handled:
            self.ctx.error_return_stmt_handled = False
            return call_cpp

        self.ctx.try_except_counter += 1
        tmp = f"__er_{self.ctx.try_except_counter}"

        if self.ctx.try_except_label:
            label = self.ctx.try_except_label
            err_opt = self.ctx.try_except_err_opt
            if err_opt:
                check = (f"if (!{tmp}.has_value()) {{ "
                         f"{err_opt} = std::move({tmp}.error()); "
                         f"goto {label}; }}")
            else:
                check = f"if (!{tmp}.has_value()) goto {label};"
        elif self.ctx.current_error_return:
            check = f"if (!{tmp}.has_value()) return ::tpy::make_unexpected({tmp}.error());"
        else:
            check = f'if (!{tmp}.has_value()) ::tpy::tpy_panic("unhandled error return");'

        # Non-value types: return pointer from statement expression, deref
        # outside. The pointer survives the scope (points to original object
        # via val_or_ref). Dereferencing gives an lvalue for method chains.
        # Value types: move out directly.
        ret_type = fi.return_type
        if ret_type and not ret_type.is_value_type() and not isinstance(ret_type, VoidType):
            return f"(*({{ auto {tmp} = {call_cpp}; {check} &::tpy::unwrap_ref(*{tmp}); }}))"
        return f"({{ auto {tmp} = {call_cpp}; {check} ::tpy::unwrap_ref_move(*{tmp}); }})"

    def _gen_call(self, expr: TpyCall) -> str:
        """Generate function call code."""
        # __call__ dispatch: delegate to method call codegen
        if expr.dunder_call is not None:
            return self._gen_method_call(expr.dunder_call)
        # Expression callees: callbacks[0](x), get_handler()(x), etc.
        if not isinstance(expr.func, TpyName):
            return self._gen_expr_callee(expr)
        # typing.cast(T, x): runtime check + extract for Any sources;
        # static-only no-op for everything else (matches CPython).
        if expr.cast_target_type is not None:
            source_code = self.gen_expr(expr.args[1])
            if expr.cast_source_is_any:
                target_cpp = expr.cast_target_type.to_cpp()
                return f"::tpy::any_cast_or_panic<{target_cpp}>({source_code})"
            return source_code
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
            # Any narrowing (D15): typeid-based check. The runtime value
            # of an empty/moved-from Any has no value() and is *not*
            # treated as any concrete type, so the has_value() guard is
            # essential. NoneType extracts as typeid(std::nullptr_t).
            # Tuple form (isinstance(x, (A, B))) packs the check types
            # into a UnionType -- emit an OR of per-member typeid checks
            # rather than typeid(std::variant<A, B>) which would never
            # match the cell's stored typeid.
            var_decl = self.ctx.lookup_var_type(expr.isinstance_var)
            if isinstance(var_decl, AnyType):
                check_type = expr.isinstance_type
                var_ref = expr.isinstance_var
                if isinstance(check_type, UnionType):
                    typeid_checks = " || ".join(
                        f"{var_ref}.value.type() == typeid({self.types.type_to_cpp(m)})"
                        for m in check_type.members
                    )
                    return f"({var_ref}.value.has_value() && ({typeid_checks}))"
                check_cpp = self.types.type_to_cpp(check_type)
                return (f"({var_ref}.value.has_value() && "
                        f"{var_ref}.value.type() == typeid({check_cpp}))")
            # holds_alternative needs the original variant; narrowed_vars
            # aliases (extracted member refs or std::get expressions) are not
            # variants, so we deliberately skip that lookup here.
            orig_var = expr.isinstance_var
            name_node = TpyName(orig_var)
            var_ref = self.gen_expr_deref(name_node) if self.ctx.is_indirect_name(name_node) else orig_var
            # Tuple form: isinstance(x, (A, B)) -> union of check types
            if isinstance(expr.isinstance_type, UnionType):
                check_members = list(expr.isinstance_type.members)
            else:
                check_members = [expr.isinstance_type]
            if orig_var in self.ctx.ptr_variant_locals:
                const_pfx = "const " if orig_var in self.ctx.const_indirect_locals else ""
                checks = [
                    f"std::holds_alternative<{const_pfx}{self.types.type_to_cpp(m)}*>({var_ref})"
                    for m in check_members
                ]
            else:
                var_decl_type = self.ctx.var_types.get(orig_var)
                get_ref = self.ctx.variant_data_expr(var_ref, var_decl_type) if var_decl_type else var_ref
                checks = [
                    f"std::holds_alternative<{self.types.type_to_cpp(m)}>({get_ref})"
                    for m in check_members
                ]
            if len(checks) == 1:
                return checks[0]
            return "(" + " || ".join(checks) + ")"
        # Enum value lookup: Color(0) -> ::tpy::EnumUtil<Color>::from_value(0).
        # enum_from_value is set only by sema when resolving via BindingKind.ENUM,
        # whose binding always points to a registered enum NominalType -- so the
        # TypeDef.enum payload is guaranteed populated here.
        if expr.enum_from_value is not None:
            enum_type = expr.enum_from_value
            cpp_type = enum_type.to_cpp()
            einfo = enum_info_of(enum_type)
            assert einfo is not None, "enum_from_value set on a type without registered EnumInfo"
            underlying_cpp = einfo.underlying_type.to_cpp()
            arg = self.gen_expr(expr.args[0])
            # BigInt needs checked conversion to the underlying type
            arg_type = self.types.get_resolved_type(expr.args[0])
            if is_big_int_type(arg_type):
                arg = f"({arg}).to_fixed_check<{underlying_cpp}>()"
            return f"::tpy::EnumUtil<{cpp_type}>::from_value({arg})"
        if (special := self._maybe_gen_special_builtin_call(expr)) is not None:
            return special
        # Type constructor with resolved @cpp_template (e.g. Int32(42), str(x))
        # Sema resolves {cpp} and class-level type params, so the template only
        # has positional {0}, {1} placeholders.  Generic constructors (call_type
        # set) need auto-move/own_iter arg handling in the full path below.
        fi = expr.resolved_function_info
        if fi and fi.cpp_template and not expr.call_type:
            result_type = self.ctx.get_expr_type(expr) or fi.return_type
            if result_type and result_type is not VOID:
                gen_args = [self.builtins._gen_expr_deref(arg, ptype)
                            for arg, (_, ptype) in zip(expr.args, fi.params)]
                return self.builtins.gen_call_from_fi(
                    fi, None, gen_args, type_args=expr.inferred_type_args)
        # print() maps to std::printf
        if expr.func_name == "print":
            return self.builtins.gen_print(expr.args, expr.kwargs)
        # ord("X") with single-char string literal -> int constant
        if expr.func_name == "ord" and len(expr.args) == 1:
            arg = expr.args[0]
            if isinstance(arg, TpyStrLiteral) and len(arg.value) == 1:
                return str(ord(arg.value))
        # Check for imported function (builtins or from X import Y -> Y())
        # Builtins (len, pow, etc.) are registered in imported_names by the analyzer.
        if expr.func_name in self.ctx.analyzer.imported_names:
            is_shadowed = (expr.func_name in self.ctx.declared_vars or
                           expr.func_name in self.ctx.global_names or
                           self.ctx.analyzer.registry.get_function(expr.func_name) is not None or
                           self.ctx.analyzer.registry.get_record(expr.func_name) is not None)
            if not is_shadowed:
                module_name, func_name = self.ctx.analyzer.imported_names[expr.func_name]
                # Check for module function
                module_info = self.ctx.analyzer.registry.get_module(module_name)
                if module_info and func_name in module_info.functions:
                    return self.builtins.gen_template_or_native_call(
                        expr.args, module_info.functions[func_name],
                        fi=expr.resolved_function_info, type_args=expr.inferred_type_args)
        # Nested def local: call the lambda variable directly
        if expr.func_name in self.ctx.nested_def_locals:
            fi = expr.resolved_function_info
            gen_args = []
            if fi:
                for arg, (_, ptype) in zip(expr.args, fi.params):
                    gen_args.append(self.gen_call_arg(arg, ptype))
            else:
                for arg in expr.args:
                    gen_args.append(self.gen_expr(arg))
            return f"{escape_cpp_name(expr.func_name)}({', '.join(gen_args)})"

        # Check if this is a function call that needs argument conversion
        func_infos = self.ctx.analyzer.registry.get_function(expr.func_name)
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

            # @cpp_template / @native(function=True): dispatch via template or native call
            if func_info.cpp_template or func_info.native_function:
                return self.builtins.gen_template_or_native_call(
                    expr.args, [func_info],
                    fi=expr.resolved_function_info, type_args=expr.inferred_type_args)

            gen_args = []
            dcbp = func_info.deep_const_borrow_params
            for arg, (pname, ptype) in zip(expr.args, func_info.params):
                # Strip Ref wrapper -- Ref is a sema annotation; codegen handles
                # reference semantics through is_value_type() / type traits.
                ptype = unwrap_ref_type(ptype)
                # Resolve TypeParamRef for generic functions
                resolved_ptype = self.types.substitute_type_params(ptype, type_subst) if type_subst else ptype

                # T() default-construction: emit ConcreteType{}
                if isinstance(arg, TpyTypeParamConstruct):
                    gen_args.append(f"{self.types.type_to_cpp(resolved_ptype)}{{}}")
                    continue

                # *args pack: materialize as std::array temp + std::span
                if isinstance(arg, TpyVarargPack):
                    gen_args.append(self._gen_vararg_pack(arg))
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
                # because C++ can't bind rvalue to non-const lvalue reference.
                # TypeParamRef generates param_val_or_ref_t<T> which is T& for
                # object types and const T& for value types -- both need a temp
                # when the callee captures by reference (e.g. generators).
                elif (resolved_ptype.is_ref_param() or isinstance(ptype, TypeParamRef)) and self.ctx.is_temporary_expr(arg):
                    init_expr = self.gen_expr(arg, resolved_ptype)
                    # Child -> Parent upcast: declare the temp with the child's
                    # type so subtype data isn't sliced; C++ binds the parent
                    # reference via implicit upcast.
                    temp_type = resolved_ptype
                    if isinstance(resolved_ptype, NominalType) and resolved_ptype.is_user_record:
                        arg_type = self.ctx.get_expr_type(arg)
                        if (isinstance(arg_type, NominalType) and arg_type.is_user_record
                                and arg_type != resolved_ptype
                                and self.ctx.analyzer.registry.is_subclass_of(arg_type, resolved_ptype)):
                            temp_type = arg_type
                    temp_name = self.ctx.temps.create(temp_type, init_expr)
                    gen_args.append(temp_name)
                # Union params: wrap concrete member type in variant
                elif (union_arg := self._gen_union_arg(arg, resolved_ptype,
                                                        is_readonly_target=func_info.is_readonly)) is not None:
                    gen_args.append(union_arg)
                else:
                    arg_idx = len(gen_args)
                    tcb = dcbp is not None and arg_idx in dcbp
                    gen_args.append(self.gen_call_arg(arg, resolved_ptype,
                                                      target_const_borrow=tcb))

            # Determine function name
            # Literal overload flattening: use mangled name for literal stubs
            # (only when stubs have different return types, triggering per-literal specialization)
            is_literal_mangled = (
                len(func_infos) > 1
                and expr.resolved_function_info is not None
                and any(isinstance(p.type, LiteralType) for p in expr.resolved_function_info.params)
            )
            if is_literal_mangled:
                mangled = literal_mangled_name(expr.func_name, expr.resolved_function_info)
            else:
                mangled = expr.func_name

            func_cpp_name = escape_cpp_name(mangled)
            if func_info.is_native_c or func_info.is_extern_c:
                # @native(binding="C") / @export(binding="C"): C-linkage
                # symbols are declared `extern "C"` in the calling module's
                # namespace (via re-declaration in the generated header),
                # so unqualified lookup resolves them. A leading `::` would
                # force global-scope lookup and miss the namespace-scoped
                # declaration.
                func_cpp_name = func_info.native_name or func_info.name
            elif func_info.is_native_import:
                # @native (C++ linkage): emit absolute-qualified so C++
                # unqualified lookup can't bind to a lexical collision in
                # the caller's scope (class method, namespace-member, ADL).
                func_cpp_name = qualify_native_name(func_info.native_name or func_info.name)
            elif (qual := lookup_imported(
                    self.ctx.analyzer.ctx.module_attributes,
                    expr.func_name, SymbolKind.FUNCTION)) is not None:
                # Cross-module call: qualify via the attribute table.
                source_module, qual_name = qual
                emit_name = mangled if is_literal_mangled else qual_name
                func_cpp_name = qualified_cpp_name(source_module, emit_name)
            elif expr.func_name in self.ctx.analyzer.imported_names:
                # Implicit builtin-module function (e.g. pure-TPy helper in
                # tpystd::builtins). Only qualify when the resolved function
                # actually lives in that imported module -- if the user
                # defined a local shadowing function, func_info.qualified_name
                # points to the local module and we leave emission
                # unqualified (C++ picks up the local definition by name).
                # Forward-looking: all current builtins are @native or
                # @cpp_template (handled above), so this branch is inert
                # today but unblocks pure-TPy builtins.
                source_module, original_name = self.ctx.analyzer.imported_names[expr.func_name]
                fi_qname = func_info.qualified_name or ""
                if fi_qname.startswith(source_module + "."):
                    func_cpp_name = qualified_cpp_name(source_module, mangled if is_literal_mangled else original_name)

            # For generic TPy functions, emit explicit type args to avoid C++ deduction
            # issues with ::tpy::param_val_or_ref_t<T> parameters.  Skip for @native
            # functions -- their C++ signatures use natural parameter types so
            # template argument deduction works correctly.
            if func_info.is_generic() and expr.inferred_type_args and not func_info.is_native_import:
                type_args_str = ", ".join(
                    self.types.type_to_cpp_stored(t) for t in expr.inferred_type_args)
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
                    and (expr.resolved_function_info.cpp_template
                         or expr.resolved_function_info.native_function)):
                ctor = expr.resolved_function_info
                # float("nan"/"inf"/...) -> constexpr numeric_limits fold.
                folded = self.builtins._try_float_str_fold(ctor, expr.args)
                if folded is not None:
                    return folded
                gen_args = []
                for a, (_, ptype) in zip(expr.args, ctor.params):
                    gen_args.append(self.gen_call_arg(a, ptype, inline_template=True))
                return self.builtins.gen_call_from_fi(ctor, None, gen_args)
            # Look up resolved init params for auto-move on Own[T] params
            init_params = []
            call_type = expr.call_type
            record_name = call_type.name if isinstance(call_type, NominalType) else None
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
        if record_info := self.ctx.analyzer.registry.get_record(expr.func_name):
            # Bare-name primitive constructors (`Float32(s)`, `Int32(s)`,
            # ...) reach here when sema resolved the matching @cpp_template
            # / @native(function=True) __init__ overload but the parser
            # didn't set call_type (only subscript-form calls like
            # `Stack[Int32]()` set it). Honor the resolved overload --
            # falling through to the @native record-name path below would
            # emit `float(arg)` / `int32_t(arg)`, a functional cast that
            # doesn't compile against a std::string arg.
            ctor_fi = expr.resolved_function_info
            if ctor_fi and (ctor_fi.cpp_template or ctor_fi.native_function):
                gen_args = [self.gen_call_arg(a, ptype, inline_template=True)
                            for a, (_, ptype) in zip(expr.args, ctor_fi.params)]
                return self.builtins.gen_call_from_fi(ctor_fi, None, gen_args)
            init_info = record_info.get_method("__init__")
            init_params = init_info.params if init_info else []
            # TypedDict/native records without __init__: use init_params for type hints
            if not init_params and record_info.init_params:
                init_params = [ParamInfo(n, t) for n, t, _ in record_info.init_params]
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
            # Native records: use native C++ name (always set on is_native
            # records by registration, defaulting to ::<class_name> when no
            # explicit @native("rename") was given).
            if record_info.is_native:
                cpp_name = record_info.native_name
                # @native_c: aggregate init (POD struct)
                if record_info.is_native_c:
                    return f"{cpp_name}{{{args}}}"
                # @native: constructor call (C++ class)
                return f"{cpp_name}({args})"
            # Cross-module constructor: qualify to the declaring module.
            qual = self.ctx.analyzer.registry.imported_record_qualification(
                expr.func_name, self.ctx.analyzer.ctx.module_name)
            if qual is not None:
                source_module, original_name = qual
                return f"{qualified_cpp_name(source_module, original_name)}({args})"
            return f"{expr.func_name}({args})"
        # Callable variable call (possibly narrowed from Optional[Callable])
        fi = expr.resolved_function_info
        if fi and (fi.cpp_template or fi.native_function):
            # float("nan"/"inf"/...) -> constexpr numeric_limits fold.
            folded = self.builtins._try_float_str_fold(fi, expr.args)
            if folded is not None:
                return folded
            gen_args = [self.gen_call_arg(a, ptype)
                        for a, (_, ptype) in zip(expr.args, fi.params)]
            return self.builtins.gen_call_from_fi(fi, None, gen_args)
        if fi:
            gen_args = []
            for arg, (_, ptype) in zip(expr.args, fi.params):
                gen_args.append(self.gen_call_arg(arg, ptype))
            args = ", ".join(gen_args)
        else:
            args = ", ".join(self.gen_expr(a) for a in expr.args)
        func_name = escape_cpp_name(expr.func_name)
        # Check if the declared type is Optional[Callable] -- needs .value() unwrap
        declared = (self.ctx.current_func_params.get(expr.func_name)
                    or self.ctx.var_types.get(expr.func_name))
        if (declared is not None
                and isinstance(declared, OptionalType)
                and isinstance(declared.inner, CallableType)):
            return f"{func_name}.value()({args})"
        return f"{func_name}({args})"

    def _gen_expr_callee(self, expr: TpyCall) -> str:
        """Generate code for expression callees: callbacks[0](x), get_fn()(x), etc."""
        callee_cpp = self.gen_expr(expr.func)
        fi = expr.resolved_function_info
        if fi:
            gen_args = []
            for arg, (_, ptype) in zip(expr.args, fi.params):
                gen_args.append(self.gen_call_arg(arg, ptype))
            args = ", ".join(gen_args)
        else:
            args = ", ".join(self.gen_expr(a) for a in expr.args)
        return f"({callee_cpp})({args})"

    def _gen_method_call(self, expr: TpyMethodCall) -> str:
        """Generate method call code."""
        # TypedDict: td.get("key") / td.get("key", default)
        if expr.typed_dict_get_field is not None:
            obj = self.gen_expr(expr.obj)
            if self.ctx.is_indirect_name(expr.obj):
                obj = f"(*{obj})"
            cpp_field = escape_cpp_name(expr.typed_dict_get_field)
            has_default = len(expr.args) == 2
            if has_default:
                default = self.gen_expr(expr.args[1])
                if expr.typed_dict_get_optional:
                    # value_or needs a type implicitly convertible to the optional's
                    # inner type; string_view is not implicitly convertible to
                    # std::string, so wrap when the field stores an owned string
                    result_type = self.types.get_resolved_type(expr)
                    if is_str_type(result_type) or is_string_type(result_type):
                        default = f"std::string({default})"
                    return f"{obj}.{cpp_field}.value_or({default})"
                else:
                    # The field is statically present; the default is evaluated
                    # for side-effect-order consistency but its value is unused.
                    # Cast to void so -Wunused-value stays quiet.
                    return f"((void){default}, {obj}.{cpp_field})"
            else:
                if expr.typed_dict_get_optional:
                    return f"{obj}.{cpp_field}"
                else:
                    # Check if the field is already Optional in C++ (total=True
                    # with explicit Optional[T] annotation) -- no wrapping needed
                    result_type = self.types.get_resolved_type(expr)
                    obj_type = self.types.get_resolved_type(expr.obj)
                    rec = self.ctx.analyzer.registry.get_record_for_type(obj_type)
                    field_already_optional = False
                    if rec:
                        for fld in rec.fields:
                            if fld.name == expr.typed_dict_get_field:
                                field_already_optional = isinstance(fld.type, OptionalType)
                                break
                    if field_already_optional:
                        return f"{obj}.{cpp_field}"
                    return f"std::make_optional({obj}.{cpp_field})"
        # Nested record constructor: Outer.Inner(args) -> Outer::Inner(args)
        if expr.is_nested_constructor and expr.nested_type_name:
            cpp_name = NominalType(expr.nested_type_name).to_cpp()
            args = ", ".join(self.gen_expr(a) for a in expr.args)
            if expr.kwargs:
                kwarg_parts = [self.gen_expr(v) for v in expr.kwargs.values()]
                if args:
                    args = ", ".join([args] + kwarg_parts)
                else:
                    args = ", ".join(kwarg_parts)
            return f"{cpp_name}({args})"
        # Nested enum from_value: Outer.Kind(v) -> Outer::Kind from_value
        if expr.is_nested_enum_constructor and expr.nested_type_name:
            cpp_name = NominalType(expr.nested_type_name).to_cpp()
            arg = self.gen_expr(expr.args[0])
            return f"::tpy::EnumUtil<{cpp_name}>::from_value({arg})"
        # Callable-typed field invocation: obj.field(args) -> obj.field(args)
        if expr.is_callable_field:
            obj_code = self.gen_expr_deref(expr.obj)
            field_name = escape_cpp_name(expr.method)
            args = ", ".join(self.gen_expr(a) for a in expr.args)
            # Check if the field is Optional[Callable] -- need .value() to unwrap
            obj_type = self.types.get_resolved_type(expr.obj)
            if isinstance(obj_type, NominalType):
                rec = self.ctx.analyzer.registry.get_record(obj_type.name)
                if rec:
                    for fld in rec.fields:
                        if fld.name == expr.method:
                            if isinstance(fld.type, OptionalType):
                                return f"{obj_code}.{field_name}.value()({args})"
                            break
            return f"{obj_code}.{field_name}({args})"

        # Skip upfront arg generation when a later path will regenerate args:
        # - cpp_template methods: handled by gen_method_from_function_info
        # - user-record methods with known method: handled by TypeParamRef temp path
        # Running the first-pass AND a later path creates duplicate TempState entries.
        _fi = expr.resolved_function_info
        _skip_first_pass = (_fi is not None
                            and (_fi.cpp_template is not None or _fi.native_function))
        if not _skip_first_pass:
            _obj_type = self.types.get_resolved_type(expr.obj)
            if isinstance(_obj_type, NominalType) and _obj_type.is_user_record:
                _ri = self.ctx.analyzer.registry.get_record_for_type(_obj_type)
                if _ri and _ri.get_method(expr.method):
                    _skip_first_pass = True
        _is_native_stub = _fi is not None and bool(_fi.native_name or _fi.cpp_template)
        if not _skip_first_pass and _fi:
            params = expr.resolved_function_info.params
            gen_args = []
            dcbp = _fi.deep_const_borrow_params
            for i, arg in enumerate(expr.args):
                ptype = params[i].type if i < len(params) else None
                if isinstance(arg, TpyTypeParamConstruct):
                    assert ptype is not None, f"No param type for TpyTypeParamConstruct at arg {i}"
                    gen_args.append(f"{self.types.type_to_cpp(ptype)}{{}}")
                elif isinstance(arg, TpyVarargPack):
                    gen_args.append(self._gen_vararg_pack(arg))
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
                    # Rvalue list literal -> protocol/TypeParamRef param: must
                    # materialize to a named temp. C++ can't deduce a template
                    # parameter from a braced-init-list, and protocol-typed
                    # params expand to template T_xs&. Concrete ref-param
                    # targets (list[T]&, dict[K,V]&, etc.) accept inline
                    # braced-init natively, so don't hoist there -- it would
                    # just bloat generated code.
                    if (ptype is not None
                            and self.ctx.is_temporary_expr(arg)
                            and (is_protocol_type(ptype) or isinstance(ptype, TypeParamRef))):
                        init_expr = self.gen_expr(arg, ptype)
                        temp_name = self.ctx.temps.create(ptype, init_expr)
                        gen_args.append(temp_name)
                        continue
                    tcb = dcbp is not None and i in dcbp
                    gen_args.append(self.gen_call_arg(arg, ptype,
                                                      inline_template=_is_native_stub,
                                                      target_const_borrow=tcb))
            args = ", ".join(gen_args)
        else:
            args = ", ".join(self.gen_expr_deref(a) for a in expr.args)

        # Module-qualified static method call: m.Foo.method() or pkg.sub.Foo.method().
        # Sema sets is_static_call AND user_module_call together with a
        # TpyFieldAccess receiver (the leaf field is the class short name);
        # render as `<module_cpp>::Class::method(args)` so the C++ name is
        # fully qualified through the module namespace, not just the class.
        if (expr.is_static_call and expr.user_module_call is not None
                and isinstance(expr.obj, TpyFieldAccess)):
            fi = expr.resolved_function_info
            class_short = expr.obj.field
            if fi and fi.cpp_template:
                gen_args = [self.gen_expr(arg) for arg in expr.args]
                return self.builtins.gen_call_from_fi(
                    fi, None, gen_args, type_args=expr.inferred_type_args)
            record_info = self.ctx.analyzer.registry.find_record_by_qname(
                f"{expr.user_module_call}.{class_short}")
            if record_info and record_info.is_native and record_info.native_name:
                cpp_class = record_info.native_name
            else:
                cpp_class = qualified_cpp_name(expr.user_module_call, class_short)
            cpp_method = fi.native_name if fi and fi.native_name else escape_cpp_name(expr.method)
            static_method_targs = ""
            if expr.inferred_type_args:
                n_class = len(record_info.type_params) if record_info and record_info.type_params else 0
                class_args = expr.inferred_type_args[:n_class]
                method_args = expr.inferred_type_args[n_class:]
                if class_args:
                    type_args_str = ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in class_args)
                    cpp_class = f"{cpp_class}<{type_args_str}>"
                if method_args:
                    static_method_targs = "<" + ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in method_args) + ">"
            return f"{cpp_class}::{cpp_method}{static_method_targs}({args})"

        # Handle user module function calls: module.func() -> ::tpyapp::module::func()
        if expr.user_module_call is not None:
            fi = expr.resolved_function_info
            # @cpp_template: expand inline regardless of module origin
            if fi and fi.cpp_template:
                return self.builtins.gen_template_or_native_call(
                    expr.args, [fi], fi=fi, type_args=expr.inferred_type_args)
            if fi and (fi.is_native_import or fi.is_extern_c):
                func_name = fi.native_name or fi.name
                # @native (C++ import): symbol comes from external headers,
                # use the native name directly (not module-qualified).
                # qualify_native_name forces leading :: so lookup can't
                # bind to a namespace member or class method by accident.
                if fi.is_native:
                    return f"{qualify_native_name(func_name)}({args})"
                # @native(binding="C") / @export: extern "C" declaration lives in the
                # module namespace, use module-qualified path
                return f"{qualified_cpp_name(expr.user_module_call, func_name)}({args})"
            # Cross-module dotted call: same path as the bare-call case
            # above. The binding's defining_module already encodes the
            # chain-flattened ultimate definer; cycle-aware codegen in
            # peers' .hpp may have suppressed the `using` for
            # `expr.user_module_call`, so we cannot rely on it.
            analyzer_ctx = self.ctx.analyzer.ctx
            qual = lookup_qualified(
                analyzer_ctx.module_attributes,
                expr.method, analyzer_ctx.module_name)
            if qual is not None:
                qual_module, qual_name = qual
            else:
                qual_module = (fi.originating_module if fi and fi.originating_module
                               else expr.user_module_call)
                qual_name = fi.name if fi else expr.method
            # Emit explicit template args for generic user-module calls
            if fi and fi.is_generic() and expr.inferred_type_args:
                type_args_str = ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in expr.inferred_type_args)
                return f"{qualified_cpp_name(qual_module, qual_name)}<{type_args_str}>({args})"
            return f"{qualified_cpp_name(qual_module, qual_name)}({args})"

        # Handle builtin module function/type calls (e.g., time.time() or t.Int32() with import tpy as t)
        if expr.builtin_module_call is not None:
            module_name = expr.builtin_module_call
            if (special := self._maybe_gen_special_builtin_call(expr)) is not None:
                return special
            # Special-handling functions with cpp_template resolved by sema
            fi = expr.resolved_function_info
            if fi and fi.special_handling and fi.cpp_template:
                gen_args = [self.gen_expr_deref(arg, p.type)
                            for arg, p in zip(expr.args, fi.params)]
                return self.builtins.gen_call_from_fi(fi, None, gen_args)
            module_info = self.ctx.analyzer.registry.get_module(module_name)
            if module_info and expr.method in module_info.functions:
                return self.builtins.gen_template_or_native_call(
                    expr.args, module_info.functions[expr.method],
                    fi=expr.resolved_function_info, type_args=expr.inferred_type_args)
            # Type constructor with @cpp_template (e.g., tpy.Int32(42))
            if fi and fi.cpp_template and not fi.type_params:
                gen_args = [self.builtins._gen_expr_deref(arg, ptype)
                            for arg, (_, ptype) in zip(expr.args, fi.params)]
                return self.builtins.gen_call_from_fi(fi, None, gen_args)
            # Type constructor with @native (e.g., builtins.float("nan")). Route
            # through gen_call_from_fi with None receiver -- the receiver is a
            # module namespace, not a value, so it must not be prepended to the
            # native call's args. Falls through to gen_method_from_function_info
            # otherwise, which would emit e.g. float_from_str(builtins, "nan").
            if fi and fi.native_function and module_info and expr.method in module_info.records:
                folded = self.builtins._try_float_str_fold(fi, expr.args)
                if folded is not None:
                    return folded
                gen_args = [self.gen_call_arg(a, p.type)
                            for a, p in zip(expr.args, fi.params)]
                return self.builtins.gen_call_from_fi(fi, None, gen_args)

        # Check for builtin method with native_function or cpp_template first
        # This must be checked before the self.method() shortcut because
        # inherited builtin methods need special codegen
        if expr.resolved_function_info:
            method_info = expr.resolved_function_info
            if method_info.native_function or method_info.cpp_template:
                # Consuming native_function: wrap receiver in std::move for ownership transfer
                if method_info.is_consuming and method_info.native_function and isinstance(expr.obj, TpyName):
                    obj_expr = self.gen_expr(expr.obj)
                    if self.ctx.is_indirect_name(expr.obj):
                        obj_expr = f"std::move(*{obj_expr})"
                    else:
                        obj_expr = f"std::move({obj_expr})"
                    gen_args = [self.gen_expr_deref(a) for a in expr.args]
                    return self.builtins.gen_call_from_fi(method_info, obj_expr, gen_args)
                receiver = self._gen_builtin_method_receiver(expr)
                if expr.is_static_call and method_info.cpp_template:
                    gen_args = [self.builtins._gen_expr_deref(arg, ptype)
                                for arg, (_, ptype) in zip(expr.args, method_info.params)]
                    return self.builtins.gen_call_from_fi(
                        method_info, None, gen_args,
                        type_args=expr.inferred_type_args)
                return self.builtins.gen_method_from_function_info(receiver, expr.args, method_info)

        # Build explicit template args for generic method calls
        method_targs = ""
        if expr.inferred_type_args and not expr.user_module_call and not expr.is_static_call:
            method_targs = "<" + ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in expr.inferred_type_args) + ">"

        # Handle super().method() -> this->ParentClass::method(args).
        # Explicit `this->` makes the receiver unambiguous to readers and
        # leaves no room for a same-named local/namespace entity to capture
        # the call.
        if expr.super_parent_type is not None:
            parent_cpp = expr.super_parent_type.to_cpp()
            # C++ requires 'template' keyword before dependent template names
            template_kw = "template " if method_targs else ""
            return f"this->{parent_cpp}::{template_kw}{escape_cpp_name(expr.method)}{method_targs}({args})"
        # Handle unbound-self dispatch: BaseN.method(self, args) -> this->BaseN::method(args).
        # sema stripped `self` from expr.args so the arg shape matches the
        # resolved FunctionInfo; the explicit receiver matches the super() form.
        if expr.unbound_self_parent_type is not None:
            parent_cpp = expr.unbound_self_parent_type.to_cpp()
            template_kw = "template " if method_targs else ""
            return f"this->{parent_cpp}::{template_kw}{escape_cpp_name(expr.method)}{method_targs}({args})"
        # Handle ClassName.staticmethod() -> ClassName::staticmethod()
        if expr.is_static_call and isinstance(expr.obj, TpyName):
            # @cpp_template on static methods: expand the template directly.
            # Pass inferred_type_args so {T}-style placeholders get resolved
            # for generic static methods (e.g. Poll.ready[T] / Poll.pending[T]).
            fi = expr.resolved_function_info
            if fi and fi.cpp_template:
                gen_args = [self.gen_expr(arg) for arg in expr.args]
                return self.builtins.gen_call_from_fi(
                    fi, None, gen_args,
                    type_args=expr.inferred_type_args)
            # For native records, use the C++ class and method names. The
            # class qname is always set on is_native records (see registration);
            # method names only have a `native_name` when explicitly renamed.
            record_info = self.ctx.analyzer.registry.get_record(expr.obj.name)
            if record_info and record_info.is_native:
                cpp_class = record_info.native_name
                cpp_method = fi.native_name if fi and fi.native_name else escape_cpp_name(expr.method)
                return f"{cpp_class}::{cpp_method}({args})"
            class_name = expr.obj.name
            static_method_targs = ""
            if expr.inferred_type_args:
                # Split inferred type args into class-level and method-level
                n_class = len(record_info.type_params) if record_info and record_info.type_params else 0
                class_args = expr.inferred_type_args[:n_class]
                method_args = expr.inferred_type_args[n_class:]
                if class_args:
                    type_args_str = ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in class_args)
                    class_name = f"{class_name}<{type_args_str}>"
                if method_args:
                    static_method_targs = "<" + ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in method_args) + ">"
            return f"{class_name}::{escape_cpp_name(expr.method)}{static_method_targs}({args})"
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
                        return self.builtins.gen_template_or_native_call(
                            expr.args, module_info.functions[expr.method])
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
            # For consuming native_function calls, obj is fully resolved (deref + move).
            # Generate the call immediately to avoid double-deref in the general path.
            method_info = expr.resolved_function_info
            if method_info and method_info.native_function:
                gen_args = [self.gen_expr_deref(a) for a in expr.args]
                return self.builtins.gen_call_from_fi(method_info, obj, gen_args)

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
        if isinstance(obj_type, NominalType) and obj_type.is_user_record:
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
                    method_dcbp = method_info.deep_const_borrow_params
                    resolved_params = expr.resolved_function_info.params if expr.resolved_function_info else []
                    # Prefer method_info.params (which keeps TypeParamRef for
                    # generic methods) to preserve the temp-for-TypeParamRef
                    # check below. When the resolved overload has more params
                    # than method_info (arity-variant stubs), extend with
                    # resolved_params entries.
                    iter_params: list[tuple[str, TpyType]] = [
                        (p[0] if isinstance(p, tuple) else p.name,
                         p[1] if isinstance(p, tuple) else p.type)
                        for p in method_info.params
                    ]
                    if len(resolved_params) > len(iter_params):
                        for p in resolved_params[len(iter_params):]:
                            iter_params.append((p.name, p.type))
                    for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, iter_params)):
                        if isinstance(arg, TpyVarargPack):
                            gen_args.append(self._gen_vararg_pack(arg))
                            continue
                        ptype_bare = unwrap_ref_type(ptype)
                        if isinstance(ptype_bare, TypeParamRef) and self.ctx.is_temporary_expr(arg):
                            # Resolve TypeParamRef to actual type
                            resolved_type = type_subst.get(ptype_bare.name, ptype_bare)
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
                                # target_type controls gen_expr_deref hints:
                                # - None literals need it for nullptr vs std::nullopt
                                # - Narrowed value-optionals need it for (*x) unwrap
                                # - Tuple literals need it for per-slot capture mode
                                #   (especially pointer-form Optional slots)
                                # - Other args: None to avoid unwanted literal coercion
                                if isinstance(arg, TpyNoneLiteral):
                                    arg_target = rptype
                                elif (rptype is not None
                                        and not isinstance(rptype, OptionalType)
                                        and self._is_narrowed_value_optional(arg)):
                                    arg_target = rptype
                                elif isinstance(arg, TpyTupleLiteral):
                                    arg_target = rptype
                                else:
                                    arg_target = None
                                # @native stub methods: skip redundant copy-then-move
                                is_native_stub = bool(method_info.native_name or method_info.cpp_template)
                                tcb = method_dcbp is not None and i in method_dcbp
                                gen_args.append(self.gen_call_arg(arg, rptype, target_type=arg_target,
                                                                  inline_template=is_native_stub,
                                                                  target_const_borrow=tcb))
                    args = ", ".join(gen_args)

        # Use -> for pointer-locals/globals (T*) and pointer-typed expressions
        # (OptionalType non-value expressions like function calls return T*)
        obj_type = self.ctx.get_expr_type(expr.obj)
        is_optional_ptr = isinstance(obj_type, OptionalType) and obj_type.uses_pointer_repr()
        # Narrowed std::optional<T>: sema sees T but C++ var is still std::optional<T>.
        # Fields and comprehension variables always use std::optional<T> (not pointer
        # repr), so dereference unconditionally. Locals with pointer repr (T*) are
        # handled by is_indirect.
        is_comp_var = isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.comp_local_names
        if not isinstance(obj_type, OptionalType):
            cpp_decl = self._get_cpp_declared_type(expr.obj)
            if isinstance(cpp_decl, OptionalType) and (
                isinstance(expr.obj, TpyFieldAccess) or is_comp_var
                or self.ctx.is_storage_form_optional_source(expr.obj)
                or not cpp_decl.uses_pointer_repr()
            ):
                obj = f"(*{obj})"
        # Resolve C++ method name: @native rename > literal-mangled overload > Python name.
        # Applied in all lowering paths below (pointer, deref, plain) so native
        # renames are honored uniformly -- including property accessors, which
        # reach this function via TpyFieldAccess.property_getter_call.
        if expr.resolved_function_info and expr.resolved_function_info.native_name:
            cpp_method = expr.resolved_function_info.native_name
        elif (expr.resolved_function_info
              and any(isinstance(p.type, LiteralType) for p in expr.resolved_function_info.params)
              and self._is_overloaded_method(expr)):
            cpp_method = literal_mangled_name(expr.method, expr.resolved_function_info)
        else:
            cpp_method = escape_cpp_name(expr.method)
        deref_chain = ".__deref__()" * expr.deref_depth
        # Optional with runtime null check -- must come before deref fast path
        if expr.needs_optional_runtime_check and is_optional_ptr:
            if isinstance(expr.obj, TpyFieldAccess):
                return f"::tpy::deref_optional_check({obj}){deref_chain}.{cpp_method}{method_targs}({args})"
            # For pointer-globals with wrapper storage, this yields raw `T*`.
            ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj)
            return f"::tpy::deref_check({ptr_expr}){deref_chain}.{cpp_method}{method_targs}({args})"
        # User-defined Deref: emit .__deref__() calls before method call
        is_narrowed = (isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.narrowed_vars) or is_assign_narrowed
        if deref_chain and obj_type and not obj_type.is_pointer():
            is_indirect = self.ctx.is_indirect_name(expr.obj) and not is_narrowed
            if is_indirect or is_optional_ptr:
                return f"{obj}->{deref_chain[1:]}.{cpp_method}{method_targs}({args})"
            return f"{obj}{deref_chain}.{cpp_method}{method_targs}({args})"
        if obj_type and obj_type.is_pointer():
            if expr.ptr_non_null:
                return f"{obj}->{cpp_method}{method_targs}({args})"
            return f"::tpy::deref_check({obj}).{cpp_method}{method_targs}({args})"
        use_arrow = ((self.ctx.is_indirect_name(expr.obj) and not is_narrowed and not is_consuming)
                     or is_optional_ptr)
        accessor = "->" if use_arrow else "."
        return f"{obj}{accessor}{cpp_method}{method_targs}({args})"

    def _gen_dyn_hasattr_block(self, synth: 'TpyMethodCall') -> str:
        """Stmt-expr that yields true if __getattr__ succeeds, false on AttributeError."""
        call = self._gen_method_call(synth)
        return (
            "({ bool __ok = true; "
            f"try {{ (void)({call}); }} "
            "catch (const ::tpy::AttributeError&) { __ok = false; } "
            "__ok; })"
        )

    def _gen_dyn_getattr_default_block(
        self, synth: 'TpyMethodCall', default_expr: 'TpyExpr',
    ) -> str:
        """Stmt-expr that yields __getattr__'s result, or the default on
        AttributeError. std::optional defers initialization so
        non-default-constructible T (e.g. Own[T]) works."""
        call = self._gen_method_call(synth)
        assert synth.resolved_function_info is not None
        ret_type = synth.resolved_function_info.return_type
        default_code = self.gen_expr(default_expr, ret_type)
        ret_cpp = self.types.type_to_cpp(ret_type)
        return (
            f"({{ std::optional<{ret_cpp}> __r; "
            f"try {{ __r.emplace({call}); }} "
            f"catch (const ::tpy::AttributeError&) {{ __r.emplace({default_code}); }} "
            "std::move(*__r); })"
        )

    def _is_overloaded_method(self, expr: TpyMethodCall) -> bool:
        """Check if a method call targets an overloaded method (multiple stubs)."""
        obj_type = self.ctx.get_expr_type(expr.obj)
        if obj_type is None or not isinstance(obj_type, NominalType):
            return False
        record = self.ctx.analyzer.registry.get_record_for_type(obj_type)
        if record is None:
            return False
        return len(record.get_method_overloads(expr.method)) > 1

    def _gen_builtin_method_receiver(self, expr: TpyMethodCall) -> str:
        """Generate the receiver expression for a builtin method call (cpp_template or native_function)."""
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
            # Recursive union wrapper: access .data for variant operations
            var_decl_type = self.ctx.var_types.get(expr_obj.name)
            get_ref = self.ctx.variant_data_expr(obj_code, var_decl_type) if var_decl_type else obj_code
            return f"std::get<{cpp_type}>({get_ref})", True
        return obj_code, False

    def _native_field_cpp_name(self, obj_type, field_name: str) -> str | None:
        """Return native_field() rename for field_name on a @native record, or None."""
        # OwnType is stripped by get_expr_type before reaching codegen, so it
        # doesn't appear here; the remaining wrappers can.
        t = obj_type
        while True:
            if isinstance(t, ReadonlyType):
                t = t.wrapped
            elif isinstance(t, PtrType):
                t = t.pointee
            elif isinstance(t, OptionalType):
                t = t.inner
            else:
                break
        record = self.ctx.analyzer.registry.get_record_for_type(t)
        if record is None or not record.is_native:
            return None
        for fld in record.fields:
            if fld.name == field_name and fld.native_name is not None:
                return fld.native_name
        return None

    def _is_static_type_chain(self, node: TpyExpr) -> bool:
        """True if `node` is a chain of name/field-access nodes that all refer
        to type names (records or enums, including nested ones, and including
        cross-module via `from mod import Foo`). Such a chain names a static
        C++ type and has no runtime side effects, so the class-constant
        wrapper doesn't need a `static_cast<void>(...)` of it -- and indeed
        couldn't, since the resulting C++ would be a type-id where an
        expression is required.
        """
        if isinstance(node, TpyName):
            if not self.ctx.current_ns:
                return False
            binding = self.ctx.current_ns.lookup(node.name)
            if binding is None:
                return False
            if binding.kind in (BindingKind.RECORD, BindingKind.ENUM):
                return True
            if binding.kind == BindingKind.IMPORTED_NAME and binding.import_source:
                src_mod, src_name = binding.import_source
                registry = self.ctx.analyzer.registry
                dotted = f"{src_mod}.{src_name}"
                return (registry.find_module_record(src_mod, src_name) is not None
                        or registry.get_builtin_record(dotted) is not None
                        or registry.get_enum(dotted) is not None)
            return False
        if isinstance(node, TpyFieldAccess):
            # Module-qualified class chain: `m.Foo` or `pkg.sub.Foo` names a
            # static type even though the head (`m` or `pkg.sub`) is a module
            # binding, not a type. Try resolving the chain leading to `node`
            # as a module name; if the leaf field is a record in that module,
            # the chain names a static type.
            module_name = self._chain_to_module_name(node.obj)
            if module_name is not None:
                if self.ctx.analyzer.registry.find_module_record(
                        module_name, node.field) is not None:
                    return True
            return self._is_static_type_chain(node.obj)
        return False

    def _chain_to_module_name(self, node: TpyExpr) -> str | None:
        """Walk a TpyName/TpyFieldAccess chain looking for a module binding.

        Returns the registered module name (e.g. "factory", "pkg.sub") when the
        chain resolves to one, else None. Used by `_is_static_type_chain` to
        recognise module-qualified class references like `m.Foo` or
        `pkg.sub.Foo` as static-type heads.

        Accepts both the explicitly-bound `import m` form (MODULE binding for
        `m`) and the dotted `import pkg.sub` form, where `pkg` is unbound but
        `pkg.sub` is registered in the module registry -- mirrors the sema-
        side `_try_resolve_dotted_module` walk.
        """
        if isinstance(node, TpyName):
            if not self.ctx.current_ns:
                return None
            binding = self.ctx.current_ns.lookup(node.name)
            if binding is None:
                # Mirror sema's `_try_resolve_dotted_module`: unbound is only
                # acceptable when the name actually resolves as a registered
                # module. Without this gate, any unbound identifier would be
                # treated as a static-type chain head and suppress the
                # receiver-eval that should run on a runtime expression.
                if self.ctx.analyzer.registry.get_module(node.name) is not None:
                    return node.name
                return None
            if binding.kind == BindingKind.MODULE:
                return (binding.import_source[0]
                        if binding.import_source else node.name)
            return None
        if isinstance(node, TpyFieldAccess):
            head = self._chain_to_module_name(node.obj)
            if head is None:
                return None
            dotted = f"{head}.{node.field}"
            if self.ctx.analyzer.registry.get_module(dotted) is not None:
                return dotted
            return None
        return None

    def _class_constant_access_parts(self, expr: TpyFieldAccess) -> tuple[str | None, str]:
        """Return (receiver_eval, qualified) for a class-constant access.

        `receiver_eval` is the code that must run for `expr.obj`'s side effects
        and runtime null/optional checks before yielding the constant; `None`
        when the receiver has no observable cost (a bare `Class.X`, `obj.X`
        on a name, or a static-type chain like `pkg.Mod.X`). `qualified` is
        the fully-qualified C++ name `<owner>::<member>`. Read-side codegen
        folds the two into a GCC statement expression; write-side codegen
        emits the receiver eval as a separate statement so the qualified
        name appears as a real lvalue.
        """
        owner = expr.class_constant_owner
        assert owner is not None
        if owner.is_native and owner.native_name:
            cpp_qname = owner.native_name
        elif owner.type_params:
            # Phase 9: the C++ static is per-template-instantiation
            # (`C<int32_t>::X`), so the qname is rendered from the
            # receiver's parameterized type. Sema's rejections (bare class
            # name and subclass-of-generic in `_lookup_class_constant_owner`)
            # guarantee a typed receiver carrying the type-args by the time
            # we reach codegen.
            obj_type = self.ctx.get_expr_type(expr.obj)
            assert obj_type is not None, (
                f"generic class-constant access lost its receiver type: {expr.field}"
            )
            unwrapped = unwrap_qualifiers(obj_type)
            if isinstance(unwrapped, OptionalType):
                unwrapped = unwrapped.inner
            cpp_qname = self.types.type_to_cpp(unwrapped)
        else:
            qual = self.ctx.analyzer.registry.record_qualification(
                owner, self.ctx.analyzer.ctx.module_name)
            if qual:
                cpp_qname = qualified_cpp_name(*qual)
            else:
                # Same-module path: nested records carry dotted Python names
                # (e.g. "Outer.Inner"), so split on `.` and join with `::`
                # -- matches qualified_cpp_name's segment handling.
                cpp_qname = "::".join(escape_cpp_name(part) for part in owner.name.split("."))
        # Phase 10: `Final[T] = native_field("rename")` overrides the
        # member name on @native class constants.
        cc_field = owner.class_constants.get(expr.field)
        cpp_member = cc_field.native_name if cc_field and cc_field.native_name else escape_cpp_name(expr.field)
        qualified = f"{cpp_qname}::{cpp_member}"
        receiver_eval: str | None = None
        if expr.needs_optional_runtime_check:
            obj_cpp = self.gen_expr(expr.obj)
            if isinstance(expr.obj, TpyFieldAccess):
                receiver_eval = f"::tpy::deref_optional_check({obj_cpp})"
            else:
                ptr_expr = self.ctx.pointer_value_expr(expr.obj, obj_cpp)
                receiver_eval = f"::tpy::deref_check({ptr_expr})"
        elif not isinstance(expr.obj, TpyName) and not self._is_static_type_chain(expr.obj):
            obj_cpp = self.gen_expr(expr.obj)
            receiver_eval = f"static_cast<void>({obj_cpp})"
        return receiver_eval, qualified

    def gen_class_constant_lvalue(self, expr: TpyFieldAccess) -> tuple[str, str]:
        """Lvalue form of a class-constant write target.

        Returns (receiver_stmt, lvalue) -- the first is a leading C++
        statement (without trailing semicolon or newline) that evaluates the
        receiver exactly once for its side effects and runtime checks; empty
        when no eval is needed. `lvalue` is the bare `<owner>::<member>` name
        suitable for both LHS and RHS of an assignment / aug-assign without
        re-evaluating the receiver.
        """
        receiver_eval, qualified = self._class_constant_access_parts(expr)
        return (receiver_eval or ""), qualified

    def _gen_field_access(self, expr: TpyFieldAccess) -> str:
        """Generate field access code."""
        cpp_field = escape_cpp_name(expr.field)

        # Dotted-module variable access: `pkg.sub.X` after `import pkg.sub`.
        # Sema attached (module_qname, var_name) for the qualified emit; reuse
        # the same shape as the bare `mod.X` path below so native_cpp_name and
        # pointer-deref handling stay consistent.
        if expr.module_var_access is not None:
            module_name, var_name = expr.module_var_access
            module_info = self.ctx.analyzer.registry.get_module(module_name)
            if module_info is not None and var_name in module_info.variables:
                var_info = module_info.variables[var_name]
                if var_info.native_cpp_name is not None:
                    return var_info.native_cpp_name
                if var_info.is_pointer:
                    return f"(*{var_info.cpp_expr})"
                return var_info.cpp_expr

        # Class constant access: <cpp_qname>::<member>. Sema sets
        # `class_constant_owner` on accesses that resolve to a class constant
        # (including MRO walks where the owner is a transitive ancestor that
        # may not appear in the current module's short-name registry, so we
        # qualify directly from the RecordInfo).
        if expr.class_constant_owner is not None:
            receiver_eval, qualified = self._class_constant_access_parts(expr)
            if receiver_eval is not None:
                return f"({{ {receiver_eval}; {qualified}; }})"
            return qualified

        # Property getter: delegate to normal method call codegen.
        if expr.property_getter_call is not None:
            inner = expr.property_getter_call
            return self._post_process_call(inner, self._gen_method_call(inner))

        # D16 dynamic-attribute getattr fallback: synthesized __getattr__.
        if expr.dyn_getattr_call is not None:
            inner = expr.dyn_getattr_call
            return self._post_process_call(inner, self._gen_method_call(inner))

        # Explicit `this->` + base qualifier picks the specific ancestor
        # subobject in non-virtual MI; without it, `field` would be ambiguous.
        if expr.unbound_self_parent_type is not None:
            parent_cpp = expr.unbound_self_parent_type.to_cpp()
            return f"this->{parent_cpp}::{cpp_field}"

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
                        # native_global variables use a user-specified C++ symbol name
                        if var_info.native_cpp_name is not None:
                            return var_info.native_cpp_name
                        # Non-value-type globals are T* pointers -- dereference for value access
                        if var_info.is_pointer:
                            return f"(*{var_info.cpp_expr})"
                        return var_info.cpp_expr

                # Enum type-level member access: Color.Red -> Color::Red
                # (or ns::E::Red for @native enums; resolved via enum_cpp_name).
                # For @native enums with native_member() overrides, the
                # member's C++ enumerator name comes from EnumInfo's rename
                # map (Python name otherwise).
                if binding and binding.kind == BindingKind.ENUM:
                    cur_module = self.ctx.analyzer.ctx.module_name
                    einfo = enum_info_of(binding.enum_type)
                    member_cpp = (
                        einfo.cpp_member_name_map.get(expr.field, expr.field)
                        if einfo is not None else expr.field
                    )
                    return f"{enum_cpp_name(binding.enum_type, cur_module, einfo=einfo)}::{member_cpp}"

                # Nested type access on a record: Container.Kind -> Container::Kind
                if binding and binding.kind in (BindingKind.RECORD, BindingKind.IMPORTED_NAME):
                    dotted = f"{expr.obj.name}.{expr.field}"
                    nested_enum = self.ctx.analyzer.registry.get_enum(dotted)
                    if nested_enum is not None:
                        cur_module = self.ctx.analyzer.ctx.module_name
                        return enum_cpp_name(nested_enum, cur_module)
                    if self.ctx.analyzer.registry.get_record(dotted) is not None:
                        # Cross-module: qualify to declaring module.
                        rec_qual = self.ctx.analyzer.registry.imported_record_qualification(
                            dotted, self.ctx.analyzer.ctx.module_name)
                        if rec_qual is not None:
                            return qualified_cpp_name(*rec_qual)
                        return dotted.replace(".", "::")

        # Chained nested type access: Outer.Mid.Inner -> Outer::Mid::Inner
        if isinstance(expr.obj, TpyFieldAccess):
            expr_type = self.ctx.get_expr_type(expr)
            if is_enum_type(expr_type) and "." in expr_type.name:
                # Enum member access: Container.Kind.LIST -> Container::Kind::LIST
                # (routes through enum_cpp_name to honor @native rename).
                cur_module = self.ctx.analyzer.ctx.module_name
                einfo = enum_info_of(expr_type)
                member_cpp = (
                    einfo.cpp_member_name_map.get(expr.field, expr.field)
                    if einfo is not None else expr.field
                )
                return f"{enum_cpp_name(expr_type, cur_module, einfo=einfo)}::{member_cpp}"

        obj = self.gen_expr(expr.obj)
        # Assignment narrowing: inline std::get<T> for member access only
        obj, is_assign_narrowed = self._apply_assign_narrowing(expr.obj, obj)
        # Check if obj is a pointer type or global - use -> instead of .
        obj_type = self.ctx.get_expr_type(expr.obj)

        # @native record field rename: native_field("m_x") overrides the Python name.
        native_cpp_field = self._native_field_cpp_name(obj_type, expr.field)
        if native_cpp_field is not None:
            cpp_field = native_cpp_field

        # Enum instance property access: c.name, c.value
        actual_obj_type = obj_type
        if isinstance(actual_obj_type, ReadonlyType):
            actual_obj_type = actual_obj_type.wrapped
        if isinstance(actual_obj_type, OwnType):
            actual_obj_type = actual_obj_type.wrapped
        if is_enum_type(actual_obj_type):
            if expr.field == "name":
                cpp_type = actual_obj_type.to_cpp()
                return f"::tpy::EnumUtil<{cpp_type}>::name({obj})"
            elif expr.field == "value":
                # is_enum_type == True implies the TypeDef was registered via
                # attach_dynamic_type_def(..., enum=EnumInfo(...)), so the
                # enum payload is always present here.
                einfo = enum_info_of(actual_obj_type)
                assert einfo is not None, "is_enum_type==True but enum_info_of returned None"
                return f"static_cast<{einfo.underlying_type.to_cpp()}>({obj})"

        # Narrowed vars (from isinstance std::get) are direct references, not pointers
        is_narrowed = (isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.narrowed_vars) or is_assign_narrowed
        is_indirect = self.ctx.is_indirect_name(expr.obj) and not is_narrowed
        is_optional_ptr = isinstance(obj_type, OptionalType) and obj_type.uses_pointer_repr()
        # Narrowed std::optional<T>: sema sees T but C++ var is still std::optional<T>.
        # Fields and comprehension variables always use std::optional<T> (not pointer
        # repr), so dereference unconditionally. Locals with pointer repr (T*) are
        # handled by is_indirect.
        is_comp_var = isinstance(expr.obj, TpyName) and expr.obj.name in self.ctx.comp_local_names
        if not isinstance(obj_type, OptionalType):
            cpp_decl = self._get_cpp_declared_type(expr.obj)
            if isinstance(cpp_decl, OptionalType) and (
                isinstance(expr.obj, TpyFieldAccess) or is_comp_var
                or self.ctx.is_storage_form_optional_source(expr.obj)
                or not cpp_decl.uses_pointer_repr()
            ):
                obj = f"(*{obj})"
        deref_chain = ".__deref__()" * expr.deref_depth
        # Optional with runtime null check -- must come before deref fast path
        if expr.needs_optional_runtime_check and is_optional_ptr:
            # Storage-form Optional source (field, container subscript,
            # storage_form_optional_locals name): the C++ shape is
            # std::optional<T>, not T*. Use deref_optional_check which
            # understands the storage shape -- raw deref_check requires
            # __deref__() and would reject std::optional<T>.
            if self.ctx.is_storage_form_optional_source(expr.obj):
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
        # When the target is a union/optional, find the matching container member
        # and use it as the effective target. The brace-init will be prefixed with
        # the explicit C++ type so the variant can deduce the alternative.
        union_prefix: str | None = None
        if isinstance(target_type, UnionType):
            # Find the unique list/array union member to use as the effective
            # target.  If multiple container members exist (e.g. list[int] |
            # list[str]), skip -- sema should have caught the ambiguity.
            container_members = [m for m in target_type.members
                                 if is_array(m) or is_list(m)]
            if len(container_members) == 1:
                union_prefix = self.types.type_to_cpp(container_members[0])
                target_type = container_members[0]
        elif (isinstance(target_type, OptionalType)
              and (is_array(target_type.inner) or is_list(target_type.inner))):
            union_prefix = self.types.type_to_cpp(target_type.inner)
            target_type = target_type.inner
        # Some types need explicit element targeting (Array, Span)
        # Others handle implicit conversions (list, etc.)
        elem_target = None
        if target_type and target_type.needs_explicit_element_target():
            elem_target = target_type.get_element_type()
        # For list literals with Optional/Union/Tuple element types, pass element target
        # so None generates std::nullopt, and tuple literals get expected types propagated.
        # DictType target means elements are key-value tuples (e.g. dict[K,V]([tuples]));
        # derive TupleType(key, value) so _gen_tuple_literal gets per-element targets.
        if elem_target is None and target_type:
            if is_dict(target_type):
                elem_target = TupleType((target_type.type_args[0], target_type.type_args[1]))
            else:
                et = target_type.get_element_type()
                if isinstance(et, (OptionalType, UnionType, TupleType, AnyType)) or is_str_type(et):
                    elem_target = et
                # Recursive union element type: pass it as elem_target so nested
                # array literals trigger union_prefix. Alias placeholders are
                # bare NominalType (no TypeDef entry) -- match by name directly.
                elif (isinstance(et, NominalType) and not et.is_protocol
                        and et.name in self.ctx.recursive_union_names):
                    alias = self.ctx.analyzer.registry.get_type_alias(et.name)
                    if alias is not None:
                        elem_target = alias
        elements = []
        with self._container_element_context():
            for e in expr.elements:
                # Use elem_target for generation (preserves old int-literal behavior, and for
                # tuple elements passes the full slot type into _gen_tuple_literal so STR hints
                # reach nested str slots).
                code = self.gen_expr_deref(e, elem_target)
                resolved = self.types.get_resolved_type(e, elem_target)
                # Any slot + int/float literal source: pass the literal
                # type through so wrap_into_any constructs an explicit
                # ::tpy::BigInt / double wrapper. Otherwise std::any
                # would store the C++ literal under typeid(int) /
                # typeid(double_literal_type), mismatching any_ops_for.
                # Typed variables retain their declared resolved type.
                if isinstance(elem_target, AnyType):
                    if isinstance(e, TpyIntLiteral):
                        resolved = IntLiteralType()
                    elif isinstance(e, TpyFloatLiteral):
                        resolved = FloatLiteralType()
                code = self._wrap_for_owned_slot(code, resolved, elem_target)
                # Pointer-variant locals/calls must be converted to value
                # variants for container storage.
                code = self._to_value_variant_if_needed(e, code, elem_target)
                elements.append(code)
        # Non-copyable element types in list (not Array) targets: use
        # make_vector instead of brace-init (std::initializer_list copies).
        # std::array uses aggregate init which handles move-only types fine.
        if elements and not is_array(target_type):
            check_type = elem_target or (target_type.get_element_type() if target_type else None)
            if check_type is None:
                check_type = self.ctx.get_expr_type(expr)
                if is_list(check_type):
                    check_type = check_type.type_args[0]
            if self._is_nocopy_container_element(check_type):
                cpp_elem = self.types.type_to_cpp(check_type)
                return self._gen_nocopy_vector(elements, cpp_elem)

        literal = f"{{{', '.join(elements)}}}"
        # std::array of std::array needs an extra brace level
        if is_array(elem_target):
            return f"{{{literal}}}"
        # Empty list needs explicit type to avoid ambiguity with T* assignment
        if not expr.elements and target_type and target_type.get_element_type() is not None:
            return f"{target_type.to_cpp()}{literal}"
        # Prefix with explicit type when inside a variant
        if union_prefix is not None:
            return f"{union_prefix}{literal}"
        # When the target is a protocol/generic param, C++ can't deduce the type
        # from a bare brace-init-list. Emit a typed literal using the target's
        # element type (e.g. Iterable[float]'s float). Going through expr_type
        # risks picking up unresolved literal element types (FloatLiteralType,
        # IntLiteralType) whose to_cpp() emits the literal value -- that's
        # what used to turn `math.prod([1.0, 2.0, 3.0])` into
        # `std::vector<1.0>{1.0, 2.0, 3.0}`.
        effective = target_type.wrapped if isinstance(target_type, OwnType) else target_type
        if is_protocol_type(effective) or isinstance(effective, TypeParamRef):
            expr_type = self.ctx.get_expr_type(expr)
            if is_array(expr_type) or is_list(expr_type):
                # When expr_type carries an unresolved literal element
                # (FloatLiteralType, IntLiteralType), its to_cpp() emits the
                # literal value into the template-arg slot (`std::vector<1.0>`).
                # Substitute the protocol's element type to produce a
                # well-formed typed literal, keeping the container flavor
                # (array/vector) the resolver chose.
                container_type = expr_type
                if (is_protocol_type(effective) and isinstance(effective, NominalType)
                        and effective.type_args and len(effective.type_args) == 1
                        and isinstance(expr_type, NominalType)
                        and expr_type.type_args):
                    elem = expr_type.type_args[0]
                    # FloatLiteralType.to_cpp() returns repr(value) ("1.0");
                    # IntLiteralType.to_cpp() similarly emits the literal int.
                    # Both are the only built-in TpyType classes whose to_cpp()
                    # intentionally returns a value instead of a type name --
                    # they should never reach codegen unresolved, but they can
                    # when a protocol target bypasses normal bidirectional
                    # inference. Extend this tuple if new "literal-like" types
                    # get added to typesys.py.
                    if isinstance(elem, (FloatLiteralType, IntLiteralType)):
                        container_type = dc_replace(
                            expr_type,
                            type_args=(effective.type_args[0],) + expr_type.type_args[1:],
                        )
                return f"{container_type.to_cpp()}{literal}"
        # Recursive union element type: emit explicit std::vector<T> so the
        # literal is self-describing when assigned to a variant (Tree __tmp = ...).
        # Bare braced-init-lists can't deduce variant constructor alternatives.
        if expr.elements:
            expr_type = self.ctx.get_expr_type(expr)
            if is_list(expr_type):
                et = expr_type.type_args[0]
                # Alias placeholder (bare NominalType) for recursive union.
                if (isinstance(et, NominalType) and not et.is_protocol
                        and et.name in self.ctx.recursive_union_names):
                    return f"{self.types.type_to_cpp(expr_type)}{literal}"
        return literal

    def _is_nocopy_container_element(self, typ: TpyType | None) -> bool:
        """Check if a container element type is non-copyable in C++.

        A type is non-copyable if the record is @nocopy, has __del__
        (which deletes copy ops), or contains nocopy type arguments.
        Also checks union members and recursive union aliases.
        """
        if typ is None:
            return False
        if self._is_cpp_noncopyable(typ):
            return True
        if isinstance(typ, UnionType):
            return any(self._is_cpp_noncopyable(m) for m in typ.members
                       if not isinstance(m, (NoneType, VoidType)))
        # Alias placeholder (bare NominalType) for recursive union.
        if (isinstance(typ, NominalType) and not typ.is_protocol
                and typ.name in self.ctx.recursive_union_names):
            alias = self.ctx.analyzer.registry.get_type_alias(typ.name)
            if isinstance(alias, UnionType):
                return any(self._is_cpp_noncopyable(m) for m in alias.members
                           if not isinstance(m, (NoneType, VoidType)))
        return False

    def _is_cpp_noncopyable(self, typ: TpyType) -> bool:
        """Check if a single type is non-copyable in C++.

        Extends sema's is_type_nocopy with __del__ (which deletes copy ops
        in C++ but isn't tracked by the sema nocopy system) and field
        propagation. Respects __copy__ escape hatch.
        """
        sema_ctx = self.ctx.analyzer.ctx
        if sema_ctx.is_type_nocopy(typ):
            return True
        record = sema_ctx.registry.get_record_for_type(typ)
        if record is None:
            return False
        if record.has_copy:
            return False
        if record.has_del:
            return True
        # Propagate through fields (e.g. BinOp with Box[Expr] field)
        for f in record.fields:
            if self._is_cpp_noncopyable(f.type):
                return True
        return False

    def _to_value_variant_if_needed(self, expr: TpyExpr, code: str, elem_target: TpyType | None) -> str:
        """Wrap ptr-variant expression with to_value_variant for container storage.

        Locals and params of non-value union type use pointer-variant repr
        (variant<T*,...>), but containers store value variants (variant<T,...>).
        """
        if elem_target is None or not isinstance(elem_target, UnionType):
            return code
        # Check if this expression produces a pointer variant
        e = expr
        while isinstance(e, TpyCoerce):
            e = e.expr
        is_ptr_src = False
        if isinstance(e, TpyName) and e.name in self.ctx.ptr_variant_locals:
            # Narrowed variables are already concrete (Cat&), not ptr-variants
            if e.name not in self.ctx.narrowed_vars:
                is_ptr_src = True
        elif isinstance(e, (TpyCall, TpyMethodCall)):
            fi = e.resolved_function_info
            if fi is not None and self.ctx.is_ptr_variant_union(fi.return_type):
                is_ptr_src = True
        if not is_ptr_src:
            return code
        val_cpp = self.types.type_to_cpp(elem_target)
        return f"::tpy::to_value_variant<{val_cpp}>({code})"

    def _gen_nocopy_vector(self, elements: list[str], cpp_elem_type: str) -> str:
        """Emit a vector construction call for nocopy element types.

        Uses ::tpy::make_vector<T>(...) which does reserve + emplace_back
        via fold expression, avoiding std::initializer_list (which copies).
        """
        return f"::tpy::make_vector<{cpp_elem_type}>({', '.join(elements)})"

    @contextlib.contextmanager
    def _container_element_context(self):
        """Set in_container_element so ternary codegen uses value-repr Optional."""
        saved = self.ctx.in_container_element
        self.ctx.in_container_element = True
        try:
            yield
        finally:
            self.ctx.in_container_element = saved

    def _gen_dict_literal(self, expr: TpyDictLiteral) -> str:
        """Generate dict literal code: {k: v, ...} -> ::tpy::ordered_map<K, V>({{k, v}, ...})"""
        dict_type = self.ctx.get_expr_type(expr)
        assert is_dict(dict_type)
        k_type, v_type = dict_type.type_args[0], dict_type.type_args[1]
        cpp_key = k_type.to_cpp()
        cpp_val = v_type.to_cpp()

        if not expr.keys:
            return f"::tpy::ordered_map<{cpp_key}, {cpp_val}>()"

        pairs = []
        with self._container_element_context():
            for k, v in zip(expr.keys, expr.values):
                k_resolved = self.types.get_resolved_type(k, k_type)
                if isinstance(k_type, AnyType):
                    if isinstance(k, TpyIntLiteral):
                        k_resolved = IntLiteralType()
                    elif isinstance(k, TpyFloatLiteral):
                        k_resolved = FloatLiteralType()
                k_cpp = self._wrap_for_owned_slot(self.gen_expr_deref(k, k_type), k_resolved, k_type)
                k_cpp = self._to_value_variant_if_needed(k, k_cpp, k_type)
                v_resolved = self.types.get_resolved_type(v, v_type)
                if isinstance(v_type, AnyType):
                    if isinstance(v, TpyIntLiteral):
                        v_resolved = IntLiteralType()
                    elif isinstance(v, TpyFloatLiteral):
                        v_resolved = FloatLiteralType()
                v_cpp = self._wrap_for_owned_slot(self.gen_expr_deref(v, v_type), v_resolved, v_type)
                v_cpp = self._to_value_variant_if_needed(v, v_cpp, v_type)
                pairs.append(f"{{{k_cpp}, {v_cpp}}}")
        return f"::tpy::ordered_map<{cpp_key}, {cpp_val}>({{{', '.join(pairs)}}})"

    def _gen_set_literal(self, expr: TpySetLiteral) -> str:
        """Generate set literal code: {a, b, ...} -> ::tpy::ordered_set<T>({a, b, ...})"""
        set_type = self.ctx.get_expr_type(expr)
        assert is_set(set_type)
        cpp_elem = set_type.type_args[0].to_cpp()

        if not expr.elements:
            return f"::tpy::ordered_set<{cpp_elem}>()"

        elem_type = set_type.type_args[0]
        elems = []
        with self._container_element_context():
            for e in expr.elements:
                e_resolved = self.types.get_resolved_type(e, elem_type)
                if isinstance(elem_type, AnyType):
                    if isinstance(e, TpyIntLiteral):
                        e_resolved = IntLiteralType()
                    elif isinstance(e, TpyFloatLiteral):
                        e_resolved = FloatLiteralType()
                e_cpp = self._wrap_for_owned_slot(self.gen_expr_deref(e, elem_type), e_resolved, elem_type)
                e_cpp = self._to_value_variant_if_needed(e, e_cpp, elem_type)
                elems.append(e_cpp)
        return f"::tpy::ordered_set<{cpp_elem}>({{{', '.join(elems)}}})"

    def _gen_list_repeat(self, expr: TpyListRepeat, target_type: TpyType | None) -> str:
        """Generate list repeat code."""
        # [elements...] * N -> repeated sequence
        # Note: Empty list repetition [] * N is collapsed to [] in the parser

        count = self.gen_expr_deref(expr.count)
        count_type = self.ctx.analyzer.get_expr_type(expr.count)
        # BigInt count needs conversion (IntLiteralType is already plain int)
        if is_big_int_type(count_type):
            count = f"{count}.to_fixed_check<int32_t>()"

        # Determine result type and element type.
        # For protocol targets and Span targets, use the resolved expr type:
        # protocols don't map to concrete C++ container types, and Span can't
        # be constructed from a range (needs contiguous memory from Array/list).
        use_resolved = (target_type is None
                        or is_protocol_type(target_type)
                        or is_span(target_type))
        if not use_resolved:
            result_type = target_type
            elem_type = target_type.get_element_type()
        else:
            result_type = self.ctx.get_expr_type(expr)
            elem_type = result_type.get_element_type() if result_type else None

        # Resolve IntLiteralType to configured default integer type.
        if isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
            if is_list(result_type):
                result_type = make_list(elem_type)

        # Use repeat_range for all list repeats (handles negative counts internally)
        repeat_elems = []
        for e in expr.elements:
            e_resolved = self.types.get_resolved_type(e, elem_type)
            if isinstance(elem_type, AnyType):
                if isinstance(e, TpyIntLiteral):
                    e_resolved = IntLiteralType()
                elif isinstance(e, TpyFloatLiteral):
                    e_resolved = FloatLiteralType()
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

        if is_array(target_type):
            return self._gen_array_comprehension(expr, elem_type, cpp_elem, target_type.type_args[1])

        comp_names = self._enter_comp_scope(expr.generator)
        try:
            with self._container_element_context():
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
        """Generate comprehension as GCC stmt-expr producing std::array with indexed assignment."""
        gen = expr.generator
        comp_names = self._enter_comp_scope(gen)
        try:
            elem_resolved = self.types.get_resolved_type(expr.element_expr, elem_type)
            insert_code = self._wrap_for_owned_slot(self.gen_expr_deref(expr.element_expr, elem_type), elem_resolved, elem_type)

            stmt_ind = INDENT * self.ctx.indent_level
            ind1 = stmt_ind + INDENT
            ind2 = ind1 + INDENT

            buf = io.StringIO()
            buf.write(f"({{\n")
            buf.write(f"{ind1}std::array<{cpp_elem}, {size}> __result;\n")

            is_range = isinstance(gen.iterable, TpyCall) and gen.iterable.func_name == "range"
            nargs = len(gen.iterable.args) if is_range else 0
            cpp_var = escape_cpp_name(gen.var)

            if is_range and nargs <= 2:
                sema_elem = builtin_modules.get_iterable_element_type(
                    self.types.get_resolved_type(gen.iterable), registry=self.ctx.analyzer.registry)
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
                buf.write(f"{ind2}__result[static_cast<std::size_t>({idx_expr})] = {insert_code};\n")
            else:
                # Array/container source -- begin/end loop with index counter
                n = self.ctx.iter_counter
                self.ctx.iter_counter += 1
                iterable_code = self.gen_expr_deref(gen.iterable)
                is_lvalue = self._comp_is_lvalue(gen.iterable)
                obj_binding = "auto&" if is_lvalue else "auto"

                sema_elem = builtin_modules.get_iterable_element_type(
                    self.types.get_resolved_type(gen.iterable), registry=self.ctx.analyzer.registry)
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
            buf.write(f"{ind1}std::move(__result);\n")
            buf.write(f"{stmt_ind}}})")

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
            with self._container_element_context():
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
            with self._container_element_context():
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

            sema_elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.analyzer.registry)
            if sema_elem is not None and isinstance(sema_elem, IntLiteralType):
                sema_elem = self.ctx.analyzer.ctx.default_int_type
            if sema_elem is None:
                sema_elem = self.ctx.analyzer.ctx.default_int_type

            buf = io.StringIO()

            is_range = isinstance(gen.iterable, TpyCall) and gen.iterable.func_name == "range"

            # Range: counter state fits in lambda init-captures
            if is_range:
                self._gen_genexpr_counter_lambda(buf, expr, gen, sema_elem, cpp_var, cpp_elem,
                                                 yield_code, stmt_ind, ind1, ind2, ind3)
                return buf.getvalue()

            # Container iterables: capture begin/end iterators into the lambda.
            # User records have synthesized begin()/end() for C++ interop
            # but are not NativeIterable -- they use the universal default
            # in for-loops. Generator expressions still use begin/end here
            # (safe for SpanIter whose begin() returns a raw span iterator).
            is_lvalue = self._comp_is_lvalue(gen.iterable)

            if is_lvalue:
                # Lvalue source outlives the generator -- IIFE with captured iterators.
                # The IIFE needs the iterable refs + any outer locals the inner
                # lambda captures (so they are in scope for re-capture).
                iter_refs = collect_name_refs(gen.iterable)
                iife_captures = self._genexpr_outer_captures(expr, gen, iter_refs)
                iife_str = iife_captures.removesuffix(", ") if iife_captures else ""
                buf.write(f"[{iife_str}]() {{\n")
                buf.write(f"{ind1}auto& __src = {iterable_code};\n")
                buf.write(f"{ind1}return ::tpy::make_generator<{cpp_elem}>(\n")
                lambda_ind = ind2
            else:
                # Non-lvalue (literals, temporaries): move the source into the
                # lambda so it outlives the generator.  Use the explicit C++
                # container type instead of auto so that literal lists become
                # std::vector (which owns data) rather than
                # std::initializer_list (which does not).
                cpp_iterable = self.types.type_to_cpp(iterable_type)
                lambda_ind = ind1
                buf.write(f"::tpy::make_generator<{cpp_elem}>(\n")

            ind2i = lambda_ind + INDENT
            ind3i = ind2i + INDENT
            outer = self._genexpr_outer_captures(expr, gen)

            if is_lvalue:
                buf.write(f"{lambda_ind}[{outer}__beg = __src.begin(), __end = __src.end()]"
                          f"() mutable -> std::optional<{cpp_elem}> {{\n")
            else:
                buf.write(f"{lambda_ind}[{outer}__src = {cpp_iterable}({iterable_code}), "
                          f"__started = false, "
                          f"__beg = {cpp_iterable}::iterator(), "
                          f"__end = {cpp_iterable}::iterator()]"
                          f"() mutable -> std::optional<{cpp_elem}> {{\n")
                buf.write(f"{ind2i}if (!__started) {{ __beg = __src.begin(); __end = __src.end(); __started = true; }}\n")

            buf.write(f"{ind2i}while (__beg != __end) {{\n")

            if gen.unpack_vars is not None:
                self._emit_inline_tuple_unpack(
                    buf, gen, sema_elem, ind3i, source_expr="*__beg++")
            else:
                binding = loop_var_binding(sema_elem, cpp_var, "*__beg++",
                                           gen.const_loop_var)
                buf.write(f"{ind3i}{binding}\n")

            self._gen_genexpr_yield(buf, gen, yield_code, cpp_elem, ind3i, ind3i + INDENT)
            buf.write(f"{ind2i}}}\n")
            buf.write(f"{ind2i}return std::nullopt;\n")
            buf.write(f"{lambda_ind}}}\n")

            if is_lvalue:
                buf.write(f"{ind1});\n")
                buf.write(f"{stmt_ind}}}()")
            else:
                buf.write(f"{stmt_ind})")

            return buf.getvalue()
        finally:
            self._exit_comp_scope(comp_names)

    def _gen_genexpr_counter_lambda(
        self, buf: io.StringIO,
        expr: TpyGeneratorExpression, gen: TpyComprehensionGenerator,
        elem_type: TpyType, cpp_var: str, cpp_elem: str,
        yield_code: str,
        stmt_ind: str, ind1: str, ind2: str, ind3: str,
    ) -> None:
        """Generate make_generator with counter-based lambda for range()."""
        range_call = gen.iterable
        assert isinstance(range_call, TpyCall)
        nargs = len(range_call.args)
        cpp_iter = elem_type.to_cpp()

        if nargs == 1:
            stop_code = self.gen_expr_deref(range_call.args[0], elem_type)
            captures = f"__i = {cpp_iter}(0), __stop = static_cast<{cpp_iter}>({stop_code})"
        elif nargs == 2:
            start_code = self.gen_expr_deref(range_call.args[0], elem_type)
            stop_code = self.gen_expr_deref(range_call.args[1], elem_type)
            captures = f"__i = static_cast<{cpp_iter}>({start_code}), __stop = static_cast<{cpp_iter}>({stop_code})"
        else:
            start_code = self.gen_expr_deref(range_call.args[0], elem_type)
            stop_code = self.gen_expr_deref(range_call.args[1], elem_type)
            step_code = self.gen_expr_deref(range_call.args[2], elem_type)
            captures = (f"__i = static_cast<{cpp_iter}>({start_code}), "
                        f"__stop = static_cast<{cpp_iter}>({stop_code}), "
                        f"__step = static_cast<{cpp_iter}>({step_code})")

        outer = self._genexpr_outer_captures(expr, gen)
        buf.write(f"::tpy::make_generator<{cpp_elem}>(\n")
        buf.write(f"{ind1}[{outer}{captures}]() mutable -> std::optional<{cpp_elem}> {{\n")

        if nargs <= 2:
            buf.write(f"{ind2}while (__i < __stop) {{\n")
            buf.write(f"{ind3}{cpp_iter} {cpp_var} = __i++;\n")
        else:
            buf.write(f'{ind2}::tpy::range_check_step_nonzero(__step);\n')
            if is_fixed_int_type(elem_type):
                buf.write(f"{ind2}::tpy::range_check_overflow<{cpp_iter}>(__i, __stop, __step);\n")
            buf.write(f"{ind2}while ((__step > 0) ? (__i < __stop) : (__i > __stop)) {{\n")
            buf.write(f"{ind3}{cpp_iter} {cpp_var} = __i;\n")
            buf.write(f"{ind3}__i += __step;\n")

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
        """Generate comprehension as GCC stmt-expr: ({ container; loop; result; })"""
        cpp_var = escape_cpp_name(gen.var)

        stmt_ind = INDENT * self.ctx.indent_level
        ind1 = stmt_ind + INDENT
        ind2 = ind1 + INDENT
        ind3 = ind2 + INDENT

        buf = io.StringIO()
        buf.write(f"({{\n")
        buf.write(f"{ind1}{container_type} __result;\n")

        iterable_code = self.gen_expr_deref(gen.iterable)
        iterable_type = self.types.get_resolved_type(gen.iterable)

        sema_elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if sema_elem is not None and isinstance(sema_elem, IntLiteralType):
            sema_elem = self.ctx.analyzer.ctx.default_int_type
        if sema_elem is None:
            sema_elem = self.ctx.analyzer.ctx.default_int_type  # fallback; sema should reject non-iterables

        if isinstance(gen.iterable, TpyCall) and gen.iterable.func_name == "range":
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
        buf.write(f"{ind1}std::move(__result);\n")
        buf.write(f"{stmt_ind}}})")

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

        iterable_type = self.types.get_resolved_type(gen.iterable)
        buf.write(f"{ind1}{obj_binding} __obj_{n} = {iterable_code};\n")
        if not skip_reserve and self._is_sized_type(iterable_type):
            # TPy view types (dict_keys/values/items, varargs) return int32_t
            # from .size(), but std::vector<T>::reserve takes size_t -- cast
            # explicitly to avoid -Wsign-conversion at the use site.
            buf.write(f"{ind1}__result.reserve(static_cast<std::size_t>(__obj_{n}.size()));\n")
        buf.write(f"{ind1}auto __beg_{n} = __obj_{n}.begin();\n")
        buf.write(f"{ind1}auto __end_{n} = __obj_{n}.end();\n")
        buf.write(f"{ind1}for (; __beg_{n} != __end_{n}; ++__beg_{n}) {{\n")

        if gen.unpack_vars is not None:
            self._gen_comp_tuple_unpack(buf, gen, elem_type, ind2, n)
        else:
            binding = loop_var_binding(elem_type, cpp_var, f"*__beg_{n}",
                                       gen.const_loop_var)
            buf.write(f"{ind2}{binding}\n")

    def _gen_comp_tuple_unpack(self, buf: io.StringIO, gen: TpyComprehensionGenerator,
                                elem_type: TpyType, ind: str, iter_n: int) -> None:
        """Generate tuple unpacking bindings inside comprehension loop body."""
        self._emit_inline_tuple_unpack(
            buf, gen, elem_type, ind, source_expr=f"*__beg_{iter_n}")

    def _emit_inline_tuple_unpack(self, buf: io.StringIO,
                                   gen: TpyComprehensionGenerator,
                                   elem_type: TpyType, ind: str,
                                   source_expr: str) -> None:
        """Emit a tuple-unpack into the comprehension/genexpr body.

        Shared by `_gen_comp_tuple_unpack` (list/dict/set comprehensions)
        and the inlined genexpr emitter in `_gen_genexpr_iter_loop` so the
        two paths can't drift.
        """
        assert gen.unpack_vars is not None
        assert isinstance(elem_type, TupleType)
        self.ctx.unpack_counter += 1
        tmp = f"__tup_{self.ctx.unpack_counter}"
        ref_binding = "const auto&" if gen.const_loop_var else "auto&"
        buf.write(f"{ind}{ref_binding} {tmp} = {source_expr};\n")
        for i, uvar in enumerate(gen.unpack_vars):
            if uvar is None:
                continue
            utype = elem_type.element_types[i]
            cpp_type = self.types.type_to_cpp(utype)
            cpp_name = escape_cpp_name(uvar)
            if utype.is_value_type():
                buf.write(f"{ind}{cpp_type} {cpp_name} = std::get<{i}>({tmp});\n")
            else:
                buf.write(f"{ind}{ref_binding} {cpp_name} = std::get<{i}>({tmp});\n")

    def _gen_comp_range_loop(self, buf: io.StringIO, gen: TpyComprehensionGenerator,
                              elem_type: TpyType, ind1: str, ind2: str,
                              cpp_var: str, iterable_code: str,
                              skip_reserve: bool = False) -> None:
        """Generate counter loop for range() in comprehension."""
        range_call = gen.iterable
        assert isinstance(range_call, TpyCall)
        nargs = len(range_call.args)
        cpp_elem = elem_type.to_cpp()
        is_bigint = is_big_int_type(elem_type)

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
        # Register the loop variable's C++ declared type so that narrowed
        # Optional deref works (e.g. `item.x if item is not None else ...`).
        iterable_type = self.types.get_resolved_type(gen.iterable)
        if iterable_type is not None:
            elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.analyzer.registry)
            if elem is not None:
                self.ctx.var_types[gen.var] = elem
                # Must register at comp-scope entry: the element-expression
                # codegen runs before the body, so the predicate must already
                # see the loop var when the consumer dispatches.
                self.ctx.register_loop_var_storage_form(gen.var, elem, gen.iterable)
                # Also register unpack var types + per-slot storage-form
                # classification (e.g. comprehension over
                # `list[tuple[P|None, Int32]]`).
                elem_peeled = unwrap_readonly(elem)
                if gen.unpack_vars and isinstance(elem_peeled, TupleType):
                    is_storage_tuple = elem_peeled.has_pointer_repr_optional_element()
                    for i, uvar in enumerate(gen.unpack_vars):
                        if uvar is None or i >= len(elem_peeled.element_types):
                            continue
                        utype = elem_peeled.element_types[i]
                        self.ctx.var_types[uvar] = utype
                        if is_storage_tuple:
                            self.ctx.register_loop_var_storage_form(
                                uvar, utype, gen.iterable)
        return names

    def _exit_comp_scope(self, names: set[str]) -> None:
        # Safe as set-difference because sema rejects nested comprehensions,
        # so no name can appear in two active comp scopes simultaneously.
        # Discards mirror the four storage-form sets that
        # `register_loop_var_storage_form` may populate at comp-scope entry,
        # symmetric with the regular for-loop's exit cleanup in
        # `_gen_loop_body`.
        self.ctx.comp_local_names -= names
        for n in names:
            self.ctx.var_types.pop(n, None)
            self.ctx.storage_form_optional_locals.discard(n)
            self.ctx.const_storage_form_optional_locals.discard(n)
            self.ctx.storage_form_tuple_locals.discard(n)
            self.ctx.const_storage_form_tuple_locals.discard(n)

    def _comp_is_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an iterable expression is a C++ lvalue."""
        return is_lvalue_iterable(
            expr, self.ctx.analyzer.registry.get_record,
            self.types.get_resolved_type)


    def _genexpr_outer_captures(self, expr: TpyGeneratorExpression,
                                gen: TpyComprehensionGenerator,
                                extra_refs: set[str] | None = None) -> str:
        """Build explicit capture prefix for outer locals referenced in a genexpr.

        Collects names from the yield expression and filter conditions.
        If *extra_refs* is given (e.g. iterable refs for an IIFE), they are
        merged in.  References to ``self`` are translated to ``this``.

        Returns a string like '&var1, &var2, ' (with trailing comma+space) if
        there are captures, or '' if there are none.  The caller appends its
        own init-captures (__i, __src, etc.) after this prefix.
        """
        refs = collect_name_refs(expr.element_expr)
        for cond in gen.conditions:
            refs |= collect_name_refs(cond)
        if extra_refs:
            refs |= extra_refs

        # "self" maps to C++ "this", not a regular local
        needs_this = "self" in refs and self.ctx.in_method
        refs.discard("self")

        # Keep only names that are function locals (not globals, not builtins,
        # not the loop variable / unpack vars shadowed by comp scope).
        locals_set = self.ctx.local_scope_names | self.ctx.nested_def_locals
        parts: list[str] = []
        if needs_this:
            parts.append("this")
        parts.extend(f"&{escape_cpp_name(n)}"
                     for n in sorted((refs & locals_set) - self.ctx.comp_local_names))

        if not parts:
            return ""
        return ", ".join(parts) + ", "

    @staticmethod
    def _is_sized_type(typ: TpyType) -> bool:
        """Check if a type has .size() in C++ (all STL containers + Span)."""
        typ = unwrap_readonly(typ)
        return (is_array(typ) or is_list(typ) or is_span(typ)
                or is_dict(typ) or is_set(typ) or is_dict_view(typ))

    def _wrap_for_owned_slot(self, code: str, resolved: TpyType, slot_type: TpyType | None) -> str:
        """Wrap a value expression to fit a container's element slot type.

        Three cases handled:

        1. **str slot, view source.** A string_view ending up in an owned-str
           slot (list[str], dict[K, str], tuple str element) is copied to
           std::string at the insertion site rather than promoting the source
           variable's type to std::string for its lifetime.

        2. **Any slot.** Heterogeneous element coercion into list[Any] /
           dict[K, Any] / set[Any] / tuple[..., Any, ...]: the per-element
           into-Any wrapping (typeid + ops table instantiation, owning
           upgrade for views) is emitted here. Note: directly nested Any
           values bypass the wrap -- they're already cells.

        3. **Tuple-of-pointer-Optional slot.** Container elements store the
           tuple in storage form (std::optional<T>); the source expression
           may be in pointer form (T*). Lift via tuple_to_storage so the
           container holds owning optionals.
        """
        if is_str_view_type(resolved):
            if is_str_type(slot_type):
                return f"std::string({code})"
            if isinstance(slot_type, OptionalType) and is_str_type(slot_type.inner):
                return f"std::string({code})"
        if isinstance(slot_type, AnyType) and not isinstance(resolved, AnyType):
            from ..coercions import wrap_into_any, CoercionContext
            return wrap_into_any(code, resolved, CoercionContext.INIT)
        if (isinstance(slot_type, TupleType)
                and slot_type.has_pointer_repr_optional_element()):
            return f"::tpy::tuple_to_storage<{slot_type.to_cpp()}>({code})"
        return code

    def _gen_tuple_literal(self, expr: TpyTupleLiteral, target_type: TpyType | None) -> str:
        """Generate tuple literal code."""
        # Own[tuple[...]] returns a value tuple whose non-value elements
        # are implicitly Own slots. Sema's `own_tuple_target()` synthesizes
        # the matching tuple shape and sets elem_capture=VALUE for those
        # elements; we mirror that decision here just to drive `_maybe_move`.
        # If sema's synthesis rule changes, this flag must change with it.
        outer_own_tuple = (isinstance(target_type, OwnType)
                           and isinstance(target_type.wrapped, TupleType))
        if outer_own_tuple:
            target_type = target_type.wrapped
        target_tuple = target_type if isinstance(target_type, TupleType) else None
        # Captured before entering the inner with-block (which always sets
        # the flag True). Storage contexts (list/dict/set element) want
        # value-form slots; borrow contexts (unannotated call args) want
        # T&/T*, with the rvalue path routed through tuple_value_to_borrow.
        in_storage_context = self.ctx.in_container_element
        # First pass: resolve per-element target / resolved types so the
        # second pass can see the final slot mode (storage vs borrow
        # fallback) when deciding whether each element is rvalue-into-
        # borrow and needs the helper.
        per_elem_targets: list[TpyType | None] = []
        per_elem_resolved: list[TpyType] = []
        for i, elem in enumerate(expr.elements):
            elem_target = target_tuple.element_types[i] if target_tuple and i < len(target_tuple.element_types) else None
            if isinstance(elem_target, RefType):
                elem_target = elem_target.wrapped
            resolved = self.types.get_resolved_type(elem, elem_target)
            if isinstance(elem_target, AnyType):
                if isinstance(elem, TpyIntLiteral):
                    resolved = IntLiteralType()
                elif isinstance(elem, TpyFloatLiteral):
                    resolved = FloatLiteralType()
            per_elem_targets.append(elem_target)
            per_elem_resolved.append(resolved)
        resolved_elem_types: list[TpyType] = [
            t if t is not None else r
            for t, r in zip(per_elem_targets, per_elem_resolved)
        ]
        has_ref_elements = any(
            (i < len(expr.elem_capture) and expr.elem_capture[i] != TupleElemCapture.VALUE)
            or isinstance(resolved_elem_types[i], TypeParamRef)
            or (not expr.elem_capture and not resolved_elem_types[i].is_value_type()
                and not isinstance(resolved_elem_types[i], OwnType))
            for i in range(len(resolved_elem_types))
        )
        if has_ref_elements:
            slot_info = self._tuple_literal_slot_info(
                resolved_elem_types, expr,
                in_storage_context=in_storage_context,
                target_provided=target_tuple is not None)
            cpp_type = f"std::tuple<{', '.join(p for _, p in slot_info)}>"
        else:
            slot_info = None
            cpp_type = self.types.type_to_cpp(TupleType(tuple(resolved_elem_types)))
        # Second pass: render each element and record rvalue-into-borrow
        # slots so the helper-tuple wrap downstream sees them.
        elem_strs: list[str] = []
        elem_value_strs: list[str] = []
        elem_value_cpps: list[str] = []
        elem_rv_borrow: list[bool] = []
        with self._container_element_context():
            for i, elem in enumerate(expr.elements):
                elem_target = per_elem_targets[i]
                resolved = per_elem_resolved[i]
                elem_capture = (expr.elem_capture[i]
                                if i < len(expr.elem_capture) else None)
                slot_mode = slot_info[i][0] if slot_info is not None else None
                # VALUE capture is the storage form (sema annotates VALUE for
                # field-context tuples). Other modes use the slot's borrow
                # form: T* for pointer-repr Optional.
                want_pointer_form = (
                    isinstance(elem_target, OptionalType)
                    and elem_target.uses_pointer_repr()
                    and elem_capture != TupleElemCapture.VALUE
                )
                if want_pointer_form:
                    elem_str = self._optional_pointer_form_value(elem, elem_target)
                else:
                    elem_str = self._wrap_for_owned_slot(self.gen_expr_deref(elem, elem_target), resolved, elem_target)
                # Matches the auto-move sema rule for `return x` of an Own var.
                # Sema annotates VALUE for any slot that takes the element by
                # value (Own[T], Own[Tuple], or field-context tuple element);
                # we mirror that universal signal to drive _maybe_move.
                captured_by_value = elem_capture == TupleElemCapture.VALUE
                slot_owned = (isinstance(elem_target, OwnType)
                              or outer_own_tuple or captured_by_value)
                slot_inner = elem_target.wrapped if isinstance(elem_target, OwnType) else elem_target
                if (slot_owned and slot_inner is not None
                        and not slot_inner.is_value_type()):
                    elem_str = self._maybe_move(elem, elem_str)
                elem_strs.append(elem_str)
                # Detect rvalue + borrow-form combinations that need the
                # helper-tuple path. None literals at pointer-form-Optional
                # slots are already handled by emitting nullptr -- they pass
                # through the helper as-is, no value-form rewrite needed.
                # The helper requires a known value-form slot type, which we
                # only have when the consumer supplied a target_tuple (so
                # elem_target / resolved are concrete, not a synthetic
                # placeholder).
                is_borrow_slot = (
                    want_pointer_form
                    or slot_mode in (TupleElemCapture.REF, TupleElemCapture.CONST_REF)
                )
                is_rv_borrow = (
                    is_borrow_slot
                    and not isinstance(elem, TpyNoneLiteral)
                    and self.ctx.is_rvalue_source(elem)
                    and elem_target is not None
                )
                elem_rv_borrow.append(is_rv_borrow)
                if is_rv_borrow:
                    # Value-form: render as the pointee value (no &-of, no
                    # nullptr-lift). Source slot type is the bare value type.
                    if isinstance(elem_target, OptionalType):
                        value_target = elem_target.inner
                    else:
                        value_target = elem_target
                    elem_value_strs.append(self.gen_expr_deref(elem, value_target))
                    elem_value_cpps.append(self.types.type_to_cpp(value_target))
                else:
                    elem_value_strs.append(elem_str)
                    elem_value_cpps.append("")
        if any(elem_rv_borrow):
            # Helper path: build a value-form source tuple and let
            # tuple_value_to_borrow take addresses / bind references inside.
            # The source tuple's lifetime extends to the end of the
            # surrounding full-expression (C++ rule), keeping those
            # addresses / refs valid through the consuming call.
            assert slot_info is not None  # is_borrow_slot implies has_ref_elements
            src_parts = [
                elem_value_cpps[i] if elem_rv_borrow[i] else slot_info[i][1]
                for i in range(len(elem_value_strs))
            ]
            src_type = f"std::tuple<{', '.join(src_parts)}>"
            if len(elem_value_strs) == 1:
                src_init = f"{src_type}({elem_value_strs[0]})"
            else:
                src_init = f"{src_type}{{{', '.join(elem_value_strs)}}}"
            return f"::tpy::tuple_value_to_borrow<{cpp_type}>({src_init})"
        # Single-element tuples use parenthesized init to avoid GCC brace-init
        # ambiguity with std::tuple constructors in C++23.
        if len(elem_strs) == 1:
            return f"{cpp_type}({elem_strs[0]})"
        return f"{cpp_type}{{{', '.join(elem_strs)}}}"

    def _tuple_literal_slot_info(
        self,
        resolved_elem_types: list[TpyType],
        expr: TpyTupleLiteral,
        in_storage_context: bool = False,
        target_provided: bool = False,
    ) -> list[tuple['TupleElemCapture', str]]:
        """Per-slot (mode, dest_cpp_part) for a tuple literal.

        Mode is the resolved TupleElemCapture (REF/CONST_REF/VALUE) and
        dest_cpp_part is the C++ slot type for the destination tuple
        (T*/const T* for pointer-repr Optional, T&/const T& for REF,
        base for VALUE, val_or_ref_t<T> for TypeParamRef under REF).

        in_storage_context distinguishes the two unannotated callers: list/
        dict/set element generation (storage; rvalues stay VALUE so the
        slot matches the container's value-form element) vs call args /
        loose contexts (borrow; rvalues become REF and codegen routes
        through tuple_value_to_borrow to bind addresses safely).
        target_provided is True when the consumer supplied a TupleType
        target. Without one we don't know whether the slot should be value
        or borrow form, so we keep the old rvalue-fallback (VALUE) -- the
        helper-tuple wrap relies on a known slot type and would otherwise
        bind a T& to an rvalue source.
        """
        info: list[tuple[TupleElemCapture, str]] = []
        for i, et in enumerate(resolved_elem_types):
            base = self.types.type_to_cpp(et)
            if i < len(expr.elem_capture):
                mode = expr.elem_capture[i]
            elif (isinstance(et, OptionalType) and et.uses_pointer_repr()):
                # Force REF so all elements end up with the same slot shape.
                # Without this, an lvalue (REF) + None (VALUE) mix yields
                # std::tuple<T*, std::optional<T>>, which won't bind to a
                # uniformly pointer-form param.
                mode = TupleElemCapture.REF
            elif not et.is_value_type() and not isinstance(et, OwnType):
                # No sema annotation (e.g. tuple in list literal or call arg).
                # Lvalues with non-value types pick REF / CONST_REF based on
                # readonly. For rvalues we split: storage contexts want
                # VALUE so the slot matches the container's element type,
                # but borrow contexts (call args) need REF -- the literal
                # itself can't bind a T& to an rvalue, so codegen wraps it
                # in tuple_value_to_borrow.
                if _is_simple_lvalue(expr.elements[i]):
                    sema_type = self.ctx.analyzer.get_expr_type(expr.elements[i])
                    mode = (TupleElemCapture.CONST_REF
                            if isinstance(sema_type, ReadonlyType)
                            else TupleElemCapture.REF)
                elif in_storage_context or not target_provided:
                    mode = TupleElemCapture.VALUE
                else:
                    mode = TupleElemCapture.REF
            else:
                mode = TupleElemCapture.VALUE
            # VALUE capture is the storage form (sema annotates VALUE for
            # field-context tuples). Other modes follow the slot's return
            # form: T* / const T* for pointer-repr Optional.
            if (isinstance(et, OptionalType) and et.uses_pointer_repr()
                    and mode != TupleElemCapture.VALUE):
                if mode == TupleElemCapture.CONST_REF:
                    info.append((mode, et.to_cpp_return_const()))
                else:
                    info.append((mode, et.to_cpp_return()))
                continue
            if isinstance(et, TypeParamRef):
                # Defer value-vs-ref to C++ instantiation time.
                # T may be val_or_ref<U> when Ref[U] is the type arg,
                # so T& would be val_or_ref<U>& -- wrong. Use the trait.
                if mode == TupleElemCapture.CONST_REF:
                    info.append((mode, f"::tpy::val_or_cref_t<{base}>"))
                else:
                    info.append((mode, f"::tpy::val_or_ref_t<{base}>"))
            elif mode == TupleElemCapture.REF:
                info.append((mode, f"{base}&"))
            elif mode == TupleElemCapture.CONST_REF:
                info.append((mode, f"const {base}&"))
            else:
                info.append((mode, base))
        return info

    def _optional_pointer_form_value(self, elem: TpyExpr,
                                      elem_target: 'OptionalType') -> str:
        """Render `elem` as a T* (pointer-form Optional) expression.

        Used wherever a slot expects T* and the source might be any of:
        a None literal, an already-pointer indirect name or ternary, a
        storage-form Optional source (record field or generator-promoted
        local) requiring optional_to_ptr, or a plain lvalue requiring
        &(...). Shared between return-statement codegen and tuple-literal
        elements with pointer-form-Optional targets.
        """
        if isinstance(elem, TpyNoneLiteral):
            return "nullptr"
        ret_expr = self.gen_expr(elem, elem_target)
        # OPTIONAL_STORAGE name (Own[Opt[P_ref]] param): C++ shape is
        # std::optional<P>&&, the slot wants T*. Lift before the
        # is_indirect_name short-circuit -- the param is also in
        # pointer_locals (for arrow field access) but here we need P*.
        if isinstance(elem, TpyName) and self.ctx.needs_optional_to_ptr_lift(elem.name):
            return f"::tpy::optional_to_ptr({ret_expr})"
        if self.ctx.is_already_pointer_source(elem):
            return ret_expr
        if isinstance(elem, TpyIfExpr):
            return ret_expr
        if self.ctx.is_storage_form_optional_source(elem):
            return f"::tpy::optional_to_ptr({ret_expr})"
        return f"&({ret_expr})"

    def _gen_subscript(self, expr: TpySubscript) -> str:
        """Generate subscript code."""
        # Enum name lookup: Color["Red"] -> ::tpy::EnumUtil<Color>::from_name("Red")
        if expr.enum_from_name is not None:
            cpp_type = expr.enum_from_name.to_cpp()
            index = self.gen_expr(expr.index)
            return f"::tpy::EnumUtil<{cpp_type}>::from_name({index})"

        # TypedDict subscript: d["key"] -> d.key (field access)
        if expr.typed_dict_field is not None:
            obj = self.gen_expr(expr.obj)
            obj = self._maybe_unwrap_narrowed_optional(
                expr.obj, obj, self.ctx.is_indirect_name(expr.obj))
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
            cpp_field = escape_cpp_name(expr.typed_dict_field)
            if expr.typed_dict_optional:
                # total=False field: unwrap with a runtime check that panics
                # KeyError. Avoid raw std::optional::value() because its
                # bad_optional_access::what() text differs across
                # libstdc++/libc++.
                return f"::tpy::typed_dict_field_check({subscript_obj}.{cpp_field})"
            return f"{subscript_obj}.{cpp_field}"

        obj = self.gen_expr(expr.obj)

        # Narrowed value-Optional: sema sees T but C++ var is still std::optional<T>
        obj = self._maybe_unwrap_narrowed_optional(
            expr.obj, obj, self.ctx.is_indirect_name(expr.obj))

        # Slice: literal a:b syntax or variable of basic_slice/slice type
        if expr.slice_function_info is not None:
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
            fi = expr.slice_function_info
            if isinstance(expr.index, TpySlice):
                slice_arg = self._gen_slice_object(expr.index, stepped=expr.is_stepped_slice)
            else:
                slice_arg = self.gen_expr(expr.index)
            return self.builtins.gen_call_from_fi(fi, subscript_obj, [slice_arg])

        obj_type = self.types.get_resolved_type(expr.obj)

        # When narrowed from Optional, use inner type for method lookup
        if isinstance(obj_type, OptionalType) and not obj_type.uses_pointer_repr():
            analyzed = self.ctx.get_expr_type(expr.obj)
            if not isinstance(analyzed, OptionalType):
                obj_type = obj_type.inner

        # Tuple subscript: std::get<N>(obj). Strip Own/Readonly/Ref so
        # Own[tuple[...]] params and similar wrapped tuple shapes hit
        # this fast-path instead of falling through to container __getitem__.
        if isinstance(unwrap_qualifiers(obj_type), TupleType):
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(expr.obj) else obj
            idx = self._extract_compile_time_index(expr.index)
            tuple_type = unwrap_qualifiers(obj_type)
            n = len(tuple_type.element_types)
            if idx < 0:
                idx += n
            result = f"std::get<{idx}>({subscript_obj})"
            # Storage-form tuple sources return std::optional<T> from std::get;
            # consumers expect T*, so lift via optional_to_ptr.
            # NOTE: `is_storage_form_optional_source` (context.py) deliberately
            # excludes TpySubscript when obj is TupleType because this branch
            # pre-lifts -- removing the lift here without updating that
            # predicate causes consumers to silently miss the lift.
            elem_type = tuple_type.element_types[idx]
            if (isinstance(elem_type, OptionalType) and elem_type.uses_pointer_repr()
                    and self.ctx.is_storage_form_source(expr.obj)):
                result = f"::tpy::optional_to_ptr({result})"
            return result

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
        index_expr = self.gen_index_expr(expr.index, index_type, view_key_target(obj_type))

        # Bounds-safe: index provably in [0, len(obj)), skip normalize_index.
        # The index type is signed (int32_t typically); built-in container
        # operator[] takes size_t, so the cast silences -Wsign-conversion.
        # Concrete user records' operator[] takes the user's declared param
        # type (typically int32_t) and don't need the cast. Protocol-bound
        # type-params keep the cast: at template instantiation the type may
        # be an STL container, where the cast is required. Compile-time
        # integer constants are exempt under GCC.
        if expr.bounds_safe:
            if isinstance(expr.index, TpyIntLiteral) or _is_concrete_user_record(
                    obj_type, self.ctx.analyzer.registry):
                return f"{subscript_obj}[{index_expr}]"
            return f"{subscript_obj}[static_cast<std::size_t>({index_expr})]"

        # Use registry lookup for __getitem__
        fi = self.builtins.get_type_method_fi(obj_type, "__getitem__")
        if fi:
            return self.builtins.gen_call_from_fi(fi, subscript_obj, [index_expr])
        # Fallback: operator[] (user records generate const operator[] from __getitem__)
        return f"{subscript_obj}[{index_expr}]"

    def gen_index_expr(self, index: TpyExpr, index_type: TpyType,
                       target_type: TpyType | None = None) -> str:
        """Generate index expression, converting BigInt indices to int32_t.

        The raw index (possibly negative) is passed through to the runtime
        helpers which handle normalization and bounds checking, matching CPython.
        target_type, when set, threads the container's key type so view-typed
        keys (BytesView/StrView) can pin literal indices to static storage.
        """
        index_expr = self.gen_expr_deref(index, target_type)
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


    def _gen_slice_object(self, sl: TpySlice, *, stepped: bool) -> str:
        """Generate a BasicSlice or Slice C++ object from slice bounds."""
        start = self._gen_optional_slice_bound(sl.lower)
        stop = self._gen_optional_slice_bound(sl.upper)
        if stepped:
            step = self._gen_optional_slice_bound(sl.step)
            return f"::tpy::Slice{{{start}, {stop}, {step}}}"
        return f"::tpy::BasicSlice{{{start}, {stop}}}"

    def _gen_slice_bound(self, expr: TpyExpr) -> str:
        """Generate a slice bound expression, converting to int32_t if needed."""
        index_type = self.ctx.analyzer.get_expr_type(expr)
        code = self.gen_expr_deref(expr)
        if self.types.is_runtime_bigint(expr, index_type):
            code = f"{code}.to_fixed_check<int32_t>()"
        return code

    def _gen_optional_slice_bound(self, expr: TpyExpr | None) -> str:
        """Generate an optional slice bound for slice construction."""
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
        if binop_result.is_reverse:
            return self.builtins.gen_call_from_fi(binop_result.method, wrapped_right, [wrapped_left])
        return self.builtins.gen_call_from_fi(binop_result.method, wrapped_left, [wrapped_right])

    def _gen_vararg_pack(self, pack: TpyVarargPack) -> str:
        """Generate C++ for a *args pack: stack array + span.

        Value types: std::array<T, N> + std::span<const T> (copies, immutable).
        Non-value types: std::array<T*, N> + tpy::ptr_span<T> (pointers, reference semantics).
        """
        elem_type = pack.element_type
        elem_cpp = self.types.type_to_cpp(elem_type)
        is_ref = not elem_type.is_value_type() and not isinstance(elem_type, TypeParamRef)

        if not pack.args:
            return f"::tpy::varargs<{elem_cpp}>()"
        sub_args = []
        for a in pack.args:
            if isinstance(a, TpyStarUnpack):
                # *expr unpacking -- wrap in varargs (direct mode from span)
                inner = self.gen_expr(a.expr)
                inner_type = self.ctx.get_expr_type(a.expr)
                if is_span(inner_type):
                    return f"::tpy::varargs<{elem_cpp}>({inner})"
                return f"::tpy::varargs<{elem_cpp}>(::tpy::as_mut_span({inner}))"
            gen = self.gen_expr(a)
            sub_args.append(f"&{gen}" if is_ref else gen)
        n = len(sub_args)
        init = ", ".join(sub_args)
        if is_ref:
            array_type = f"std::array<{elem_cpp}*, {n}>"
        else:
            array_type = f"std::array<{elem_cpp}, {n}>"
        temp = self.ctx.temps.create_typed(array_type, init, brace_init=True)
        return f"::tpy::varargs<{elem_cpp}>({temp})"

    def _gen_span_coercion(self, expr: TpyExpr, span_type: NominalType, gen_inner: str) -> str:
        """Generate std::span conversion for supported container types."""
        # Span[T] -> Span[readonly[T]]: C++ implicit conversion, no helper needed
        actual_type = self.ctx.get_expr_type(expr)
        if is_span(actual_type):
            return gen_inner
        # Spannable[T] protocol type: always uses as_span (readonly)
        if is_protocol_type(actual_type) and actual_type.qualified_name() == "tpy.Spannable":
            return f"::tpy::as_span({gen_inner})"
        # User type with __span__() method: call it directly
        if isinstance(actual_type, NominalType) and actual_type.is_user_record:
            if builtin_modules.get_span_element_type(actual_type, registry=self.ctx.analyzer.registry) is not None:
                if self.ctx.is_indirect_name(expr):
                    gen_inner = f"(*{gen_inner})"
                return f"{gen_inner}.__span__()"
        helper = "::tpy::as_span" if is_readonly_span(span_type) else "::tpy::as_mut_span"
        if isinstance(expr, TpyArrayLiteral):
            expected_array_type = make_array(span_type.type_args[0], len(expr.elements))
            array_expr = f"{expected_array_type.to_cpp()}{gen_inner}"
            return f"{helper}({array_expr})"
        # gen_inner already generated, need to check if source was global
        if self.ctx.is_indirect_name(expr):
            gen_inner = f"(*{gen_inner})"
        return f"{helper}({gen_inner})"

    def _convert_to_fixed_int_arg(self, gen_expr: str, actual_type: TpyType, expected_type: TpyType, expr: TpyExpr) -> str:
        """Convert to the target fixed-width int when a runtime BigInt may be present."""
        if is_fixed_int_type(expected_type):
            cpp_t = expected_type.to_cpp()
            if is_big_int_type(actual_type):
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
        raw_value = ""  # original (unescaped) literal content, for NUL detection
        decoded_fmt_parts: list[str] = []  # runtime view of the format string (NUL-byte length)
        args: list[str] = []
        all_literal = True

        for part in expr.parts:
            if isinstance(part, str):
                escaped = escape_cpp_string(part)
                raw_parts.append(escaped)
                raw_value += part
                # Escape braces for std::format
                fmt_parts.append(escaped.replace("{", "{{").replace("}", "}}"))
                decoded_fmt_parts.append(part.replace("{", "{{").replace("}", "}}"))
            else:
                all_literal = False
                gen_arg, arg_type = self.gen_expr_narrowed(part.expr)
                has_spec = part.format_spec is not None
                conv = part.conversion

                if has_spec:
                    placeholder = "{:" + part.format_spec + "}"
                else:
                    placeholder = "{}"
                fmt_parts.append(placeholder)
                decoded_fmt_parts.append(placeholder)

                is_user_type = (
                    (isinstance(arg_type, NominalType) and arg_type.is_user_record)
                    or isinstance(arg_type, TypeParamRef)
                )

                container_str = self._container_to_str(arg_type, gen_arg)

                # !r conversion: always wrap with repr_of (the helper that
                # routes through ADL so per-record __repr__ overrides bind).
                if container_str is not None:
                    gen_arg = container_str
                elif conv == FSTRING_CONV_REPR:
                    gen_arg = f"::tpy::repr_of({gen_arg})"
                # !s conversion on user types: wrap with __str__
                elif conv == FSTRING_CONV_STR and is_user_type:
                    gen_arg = f"::tpy::__str__({gen_arg})"
                # Wrap args that need Python-compatible formatting
                elif is_bool_type(arg_type):
                    if has_spec:
                        gen_arg = f"static_cast<int>({gen_arg})"
                    else:
                        gen_arg = f"::tpy::bool_to_str({gen_arg})"
                elif is_float64_type(arg_type) and not has_spec:
                    gen_arg = f"::tpy::float_to_str({gen_arg})"
                elif is_float32_type(arg_type) and not has_spec:
                    gen_arg = f"::tpy::float_to_str(static_cast<double>({gen_arg}))"
                elif self.types.is_runtime_bigint(part.expr, arg_type) and not has_spec:
                    gen_arg = f"({gen_arg}).to_string()"
                elif (arg_tr := int_traits_of(arg_type)) is not None and arg_tr.bits == 8:
                    gen_arg = f"static_cast<int>({gen_arg})"
                elif is_enum_type(arg_type):
                    gen_arg = f"static_cast<int>({gen_arg})"
                elif is_user_type:
                    gen_arg = f"::tpy::__str__({gen_arg})"
                elif isinstance(arg_type, UnionType):
                    # std::variant isn't std::formattable; route through the
                    # runtime visitor that dispatches __str__ per alternative.
                    gen_arg = f"::tpy::__str__({gen_arg})"

                args.append(gen_arg)

        if all_literal:
            # Pure literal f-string -- use raw parts (no brace-escaping needed).
            joined = "".join(raw_parts)
            # Embedded NUL: explicit-length std::string ctor; the
            # const-char-pointer ctor would truncate via strlen.
            if '\x00' in raw_value:
                nbytes = len(raw_value.encode('utf-8'))
                return f'std::string("{joined}", {nbytes})'
            return f'std::string("{joined}")'

        fmt_str = "".join(fmt_parts)
        args_str = ", ".join(args)
        # Embedded NUL: route through vformat with an explicit-length view.
        # std::format's consteval ctor would truncate the format string via
        # string_view(const char*) -> strlen.
        if '\x00' in raw_value:
            nbytes = len("".join(decoded_fmt_parts).encode('utf-8'))
            return (f'std::vformat(std::string_view{{"{fmt_str}", {nbytes}}}, '
                    f'std::make_format_args({args_str}))')
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
            and not self.ctx.in_container_element
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
        if is_list(result_type):
            cpp_type = self.types.type_to_cpp(result_type)
            if isinstance(expr.then_expr, TpyArrayLiteral):
                then_code = f"{cpp_type}{then_code}"
            if isinstance(expr.else_expr, TpyArrayLiteral):
                else_code = f"{cpp_type}{else_code}"

        # C++ ternary requires both branches to have the same type.
        # When arms have mismatched C++ types (one string_view, one std::string),
        # explicitly convert the string_view arm so the ternary deduces std::string.
        # Skip when the target is StrView -- wrapping would create a dangling
        # string_view pointing to a temporary std::string.
        if is_str_type(result_type) and not is_str_view_type(target_type):
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
            cpp_name = escape_cpp_name(pname)
            if expr.captures_by_value or expr.readonly_params:
                # Callable context or key function: const ref for non-value types
                cpp_type_str = CallableType._callable_param_cpp(ptype)
                cpp_type = f"{cpp_type_str} {cpp_name}"
            else:
                cpp_type = ptype.to_cpp_param(cpp_name)
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
            if expr.captures_by_value:
                refs = ", ".join(escape_cpp_name(n) for n in expr.captured_names)
            else:
                refs = ", ".join(f"&{escape_cpp_name(n)}" for n in expr.captured_names)
            capture = f"[{refs}]"
        else:
            capture = "[]"
        if isinstance(expr.inferred_return_type, VoidType):
            return f"{capture}({params_str}) {{ {body_code}; }}"
        # Explicit trailing return type -- needed for Ref[T] (C++ lambda
        # deduction strips references) and consistent for all lambdas.
        ret_type = expr.inferred_return_type
        if expr.readonly_params:
            trailing = f" -> {ret_type.to_cpp_return_const()}"
        else:
            trailing = f" -> {ret_type.to_cpp()}"
        return f"{capture}({params_str}){trailing} {{ return {body_code}; }}"

    def _gen_function_ref(self, expr: TpyName) -> str:
        """Generate C++ code for a named function used as a value."""
        # Nested def: just the local lambda variable name
        if expr.name in self.ctx.nested_def_locals:
            return escape_cpp_name(expr.name)
        fi = expr.function_ref_info
        # Build template args suffix for generic function refs
        targs = ""
        if expr.function_ref_type_args:
            targs = "<" + ", ".join(self.types.type_to_cpp(unwrap_ref_type(t)) for t in expr.function_ref_type_args) + ">"
        # Cross-module: use qualified name
        qual = lookup_imported(
            self.ctx.analyzer.ctx.module_attributes,
            expr.name, SymbolKind.FUNCTION)
        if qual is not None:
            source_module, original_name = qual
            return qualified_cpp_name(source_module, original_name) + targs
        # Native functions: use native C++ name
        if fi.is_native:
            return qualify_native_name(fi.native_name or fi.name) + targs
        if fi.is_native_c or fi.is_extern_c:
            # C-linkage: declaration is namespace-scoped extern "C"; don't
            # force global-scope lookup.
            return (fi.native_name or fi.name) + targs
        # Same-module function
        return escape_cpp_name(expr.name) + targs
