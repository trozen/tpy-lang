"""
TurboPython Code Generation Type Helpers

Type resolution and C++ type mapping utilities.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType,
    PendingListType, PendingDictType, PendingSetType, PendingViewType, ViewTypeFamily, make_list, make_dict, make_set, TypeParamRef, NominalType,
    UnionType, NoneType, VoidType, TupleType, ReadonlyType, OwnType,
    ConcreteCoroType, ConcreteFrameType, spells_readonly_arg_const,
    template_arg_cpp, varargs_elem_cpp,
    unwrap_readonly, unwrap_ref_type, is_protocol_type, resolve_int_literals,
    is_integer_type, is_float_type, is_numeric_type, is_void_like_type,
    substitute_type_params_simple,
    INT32, BIGINT, FLOAT, FLOAT32, STR, BYTES,
)
from ..parse import TpyExpr, TpyName, TpyBinOp, TpyUnaryOp, TpyCoerce, TpyCall, TpyMethodCall, TpyIntLiteral, TpyIfExpr
from ..sema.context import PENDING_CONTAINER_TYPES
from .context import qualified_cpp_name, enum_cpp_name
from . import resumable_cfg as rcfg
from ..compilation_context import get_current_compiler
from ..type_def_registry import (
    is_list,
    is_fixed_int_type, is_big_int_type, is_bool_type, is_float64_type, is_float32_type,
    is_enum_type, protocol_info_of,
)

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .protocols import ProtocolGenerator


def resolve_pending_container(typ: 'TpyType | None', analyzer) -> 'TpyType | None':
    """Resolve a PendingListType/PendingDictType/PendingSetType to its final
    container type via sema's per-literal resolution record, or None when `typ`
    is not a pending container (or has no resolution yet). The single shared
    lookup -- TypeResolver, the statement decl-type normalizer, and THIR lowering
    all consult the same SemanticContext record, so a new container kind is
    handled in one place."""
    if not isinstance(typ, PENDING_CONTAINER_TYPES):
        return None
    info = analyzer.ctx.get_container_info(typ.literal_id)
    if info and info.resolved_type:
        return info.resolved_type
    return None


def _numeric_operand(t: TpyType | None) -> bool:
    """A binop operand the numeric result-typing rules may be applied to."""
    return is_numeric_type(t) or isinstance(t, (IntLiteralType, FloatLiteralType))


class TypeResolver:
    """Type resolution and C++ type mapping utilities."""

    def __init__(self, ctx: CodeGenContext, protocols: ProtocolGenerator):
        self.ctx = ctx
        self.protocols = protocols

    def get_resolved_type(self, expr: TpyExpr, target_type: TpyType | None = None) -> TpyType:
        """Get the resolved type of an expression, handling PendingListType.

        PendingListType is used during semantic analysis but should be resolved
        to concrete Array or list types before codegen. This method looks up
        the resolved type if needed.

        Args:
            expr: The expression to get the type of.
            target_type: Optional hint for what type the expression will be coerced to.
                         Used to determine if literal+literal should be int32 or BigInt.
        """
        # Protocol isinstance narrowing: `if isinstance(x, SomeProtocol):`
        # narrows x from a union parameter to the protocol type within the
        # branch. Surface the narrower type so for-loop dispatch, `in`
        # operator, etc. pick the right peephole.
        if isinstance(expr, TpyName) and expr.name in self.ctx.protocol_narrowings:
            return unwrap_readonly(self.ctx.protocol_narrowings[expr.name])
        # Check for codegen-overridden types (e.g., loop variables). The entry
        # may still hold a deferred type (a tuple-unpack target keeps its
        # PendingStrType), so resolve it here too -- a caller receiving the
        # pending type reads as neither view nor owned, and every view-form
        # predicate keyed on this answer silently says "not a view".
        if isinstance(expr, TpyName) and expr.name in self.ctx.var_types:
            return self.resolve_type(
                unwrap_readonly(self.ctx.var_types[expr.name]))
        if isinstance(expr, TpyCoerce):
            return expr.expected_type

        # Ternary: if both branches resolve to the same codegen type, use it
        # so deductions like string_view propagate through (e.g. a if c else b).
        if isinstance(expr, TpyIfExpr):
            typ = self.ctx.analyzer.get_expr_type(expr)
            then_resolved = self.get_resolved_type(expr.then_expr)
            else_resolved = self.get_resolved_type(expr.else_expr)
            if then_resolved == else_resolved:
                return then_resolved
            # Branches differ -- resolve pending view types so the view->owned
            # wrapping check in statement codegen sees a concrete type.
            ret = unwrap_readonly(typ) if typ else typ
            if isinstance(ret, PendingViewType):
                ret = self._resolve_pending_view(ret)
            return ret

        # For binary operations, compute type using resolved operand types
        if isinstance(expr, TpyBinOp):
            # Comparisons: skip arithmetic type logic, use sema type directly.
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                typ = self.ctx.analyzer.get_expr_type(expr)
                return unwrap_readonly(typ) if typ else typ
            # Logical and/or: for operand-return semantics, resolve via
            # operand types so codegen-level deductions (e.g. string_view)
            # propagate correctly. Bool-result falls through to sema type.
            if expr.op in ("&&", "||"):
                typ = self.ctx.analyzer.get_expr_type(expr)
                if is_bool_type(typ):
                    return unwrap_readonly(typ)
                left_resolved = self.get_resolved_type(expr.left)
                right_resolved = self.get_resolved_type(expr.right)
                if left_resolved == right_resolved:
                    return left_resolved
                # Operands differ (e.g. one is string_view, other is string).
                # Resolve pending view types so callers like the view->owned
                # wrapping check in statement codegen see a concrete type.
                ret = unwrap_readonly(typ) if typ else typ
                if isinstance(ret, PendingViewType):
                    ret = self._resolve_pending_view(ret)
                return ret

            # First pass without context to detect int32 operands
            left_raw = self.get_resolved_type(expr.left)
            right_raw = self.get_resolved_type(expr.right)
            # The numeric rules below (div -> float, float precedence, fixed-int
            # and BigInt propagation) are only valid when BOTH operands are
            # numeric scalars. With a record operand the binop resolved to a
            # user dunder (e.g. __truediv__ -> record) and they would misfire;
            # fall through to sema's resolved type instead.
            if _numeric_operand(left_raw) and _numeric_operand(right_raw):
                left_analyzer_type = self.ctx.analyzer.get_expr_type(expr.left)
                right_analyzer_type = self.ctx.analyzer.get_expr_type(expr.right)
                left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
                right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)

                # If either operand is float-family, result is float (float takes precedence)
                if is_float_type(left_raw) or is_float_type(right_raw):
                    if expr.op == "div" or expr.op in ("+", "-", "*", "//", "%", "**"):
                        # float64 wins over float32
                        if is_float64_type(left_raw) or is_float64_type(right_raw):
                            return FLOAT
                        return left_raw if is_float32_type(left_raw) else right_raw

                # True division always returns float
                if expr.op == "div":
                    return FLOAT

                # Determine fixed-int context: explicit target or operand is a fixed-width int
                fixed_ctx = target_type if is_fixed_int_type(target_type) else None
                if is_fixed_int_type(left_raw) and not left_is_literal:
                    fixed_ctx = left_raw
                elif is_fixed_int_type(right_raw) and not right_is_literal:
                    fixed_ctx = right_raw
                # Second pass with context for proper literal resolution
                left_type = self.get_resolved_type(expr.left, fixed_ctx)
                right_type = self.get_resolved_type(expr.right, fixed_ctx)
                # If target is fixed-int and both operands are literals, result is that type
                if is_fixed_int_type(fixed_ctx) and left_is_literal and right_is_literal:
                    return fixed_ctx
                # Pure literal binops without fixed context use configured default-int,
                # with range-safe fallback for out-of-range results.
                if left_is_literal and right_is_literal:
                    analyzed = self.ctx.analyzer.get_expr_type(expr)
                    if isinstance(analyzed, IntLiteralType):
                        return self.ctx.analyzer.ctx.default_int_for_literal(analyzed)
                    return self.ctx.analyzer.ctx.default_int_type
                # If either operand is a fixed-width int (and other is compatible), result is that type
                if is_fixed_int_type(left_type) and (is_fixed_int_type(right_type) or isinstance(right_type, IntLiteralType)):
                    return left_type
                if is_fixed_int_type(right_type) and (is_fixed_int_type(left_type) or isinstance(left_type, IntLiteralType)):
                    return right_type
                # Otherwise, result is BigInt if either operand is BigInt.
                # For literal-literal arithmetic without stronger context, use the
                # configured default integer type.
                is_bigint_op = is_big_int_type(left_type) or is_big_int_type(right_type)
                if is_bigint_op and expr.op in ("+", "-", "*", "//", "%", "**", "&", "|", "^", "<<", ">>"):
                    return BIGINT
                if (
                    isinstance(left_type, IntLiteralType)
                    and isinstance(right_type, IntLiteralType)
                    and expr.op in ("+", "-", "*", "//", "%", "**", "&", "|", "^", "<<", ">>")
                ):
                    analyzed = self.ctx.analyzer.get_expr_type(expr)
                    if isinstance(analyzed, IntLiteralType):
                        return self.ctx.analyzer.ctx.default_int_for_literal(analyzed)
                    return self.ctx.analyzer.ctx.default_int_type

        typ = self.ctx.analyzer.get_expr_type(expr)
        # Strip ReadonlyType -- C++ doesn't use it
        typ = unwrap_readonly(typ) if typ else typ
        resolved = self._resolve_pending_container(typ) if typ else None
        if resolved is not None:
            return resolved
        if isinstance(typ, PendingViewType):
            return self._resolve_pending_view(typ)
        # Resolve IntLiteralType based on context (FixedInt/BigInt if target, else
        # configured default int type).
        if isinstance(typ, IntLiteralType):
            if target_type is not None and is_integer_type(target_type):
                return target_type
            return self.ctx.analyzer.ctx.default_int_for_literal(typ)
        # Resolve FloatLiteralType based on context (float32 if target, else float64).
        if isinstance(typ, FloatLiteralType):
            if is_float32_type(target_type):
                return FLOAT32
            return FLOAT
        # Resolve IntLiteralType in container element types
        if is_list(typ) and isinstance(typ.type_args[0], IntLiteralType):
            elem = self.ctx.analyzer.ctx.default_int_for_literal(typ.type_args[0])
            return make_list(elem)
        # Resolve IntLiteralType in tuple element types (recursively for nesting)
        if isinstance(typ, TupleType):
            resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
            resolved_elems = []
            changed = False
            for i, et in enumerate(typ.element_types):
                if isinstance(et, IntLiteralType):
                    # Use target_type's element if it's a FixedInt annotation
                    tt_elem = None
                    if isinstance(target_type, TupleType) and i < len(target_type.element_types):
                        tt_elem = target_type.element_types[i]
                    if tt_elem is not None and is_integer_type(tt_elem):
                        resolved_elems.append(tt_elem)
                    else:
                        resolved_elems.append(resolve_lit(et))
                    changed = True
                elif isinstance(et, PendingViewType):
                    resolved_elems.append(self._resolve_pending_view(et))
                    changed = True
                else:
                    resolved = resolve_int_literals(et, resolve_lit)
                    if resolved is not et:
                        changed = True
                    resolved_elems.append(resolved)
            if changed:
                return TupleType(tuple(resolved_elems))
        return typ

    def _resolve_view_storage(self, family: ViewTypeFamily, var_id: int) -> TpyType:
        """Resolve a view-vars entry to its storage form (view or owned)."""
        info = self.ctx.analyzer.ctx.view_vars(family).get(var_id)
        return info.resolved_type if info and info.resolved_type else family.owned_type

    def _resolve_pending_view(self, typ: PendingViewType) -> TpyType:
        """Resolve a PendingViewType to its concrete type."""
        return self._resolve_view_storage(typ.family, typ.var_id)

    def resolve_tuple_pending(self, tt: TupleType) -> TupleType:
        """Public entry to pending-element resolution: callers that hand a
        tuple to `convert()` (which spells via the bare `to_cpp*` methods that
        do not resolve) must resolve first, exactly as `tuple_*_cpp` do."""
        return self._resolve_tuple_pending(tt)

    def _resolve_tuple_pending(self, tt: TupleType) -> TupleType:
        """Resolve PendingViewType / IntLiteralType tuple elements to their
        concrete types. `to_cpp` / `to_cpp_return` do not resolve pending
        slots, so the borrow/storage wrap sites must do it first (e.g. a
        str loop-var element carries PendingStrType until usage-resolved)."""
        resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
        resolved = []
        for et in tt.element_types:
            if isinstance(et, PendingViewType):
                resolved.append(self._resolve_pending_view(et))
            else:
                resolved.append(resolve_int_literals(et, resolve_lit))
        return TupleType(tuple(resolved))

    def tuple_borrow_cpp(self, tt: TupleType, const: bool = False) -> str:
        """Borrow-form C++ (`std::tuple<..., T*>`) for a tuple, resolving
        pending element types first."""
        resolved = self._resolve_tuple_pending(tt)
        return resolved.to_cpp_return_const() if const else resolved.to_cpp_return()

    def tuple_storage_cpp(self, tt: TupleType) -> str:
        """Storage-form C++ (`std::tuple<..., T>` / `std::optional<T>`) for a
        tuple, resolving pending element types first."""
        return self._resolve_tuple_pending(tt).to_cpp()

    def _resolve_pending_container(self, typ: TpyType) -> TpyType | None:
        """Resolve a pending container type via the shared unified lookup.

        Returns the resolved type, or None if not a pending container.
        Falls back to a best-effort concrete type if resolution hasn't run
        (e.g. list -> ListType with resolved element type).
        """
        if not isinstance(typ, PENDING_CONTAINER_TYPES):
            return None
        resolved = resolve_pending_container(typ, self.ctx.analyzer)
        if resolved is not None:
            return resolved
        # Fallback for unresolved containers
        if isinstance(typ, PendingListType):
            elem_type = typ.element_type
            if isinstance(elem_type, IntLiteralType):
                elem_type = self.ctx.analyzer.ctx.default_int_for_literal(elem_type)
            return make_list(elem_type)
        if isinstance(typ, PendingDictType):
            return make_dict(typ.key_type, typ.value_type)
        if isinstance(typ, PendingSetType):
            return make_set(typ.element_type)
        return None

    def resolve_type(self, typ: TpyType) -> TpyType:
        """Resolve deferred types (PendingViewType, PendingListType, etc.) to concrete C++ types."""
        if isinstance(typ, PendingViewType):
            return self._resolve_pending_view(typ)
        resolved = self._resolve_pending_container(typ)
        if resolved is not None:
            return resolved
        return typ

    def substitute_type_params(self, typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
        """Substitute type parameters with concrete types for codegen.

        Delegates to the shared `substitute_type_params_simple` (typesys) --
        THIR lowering resolves call-site slots through the same function.
        """
        return substitute_type_params_simple(typ, subst)

    @staticmethod
    def involves_variables(expr: TpyExpr) -> bool:
        """Check if an expression involves any variable references. Static --
        the literal fold's syntactic guard, one walk shared by every
        caller."""
        if isinstance(expr, TpyCoerce):
            return TypeResolver.involves_variables(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return (TypeResolver.involves_variables(expr.left)
                    or TypeResolver.involves_variables(expr.right))
        if isinstance(expr, TpyUnaryOp):
            return TypeResolver.involves_variables(expr.operand)
        if isinstance(expr, TpyCall):
            return True  # Function calls may return BigInt
        if isinstance(expr, TpyMethodCall):
            return True
        # Default to True for safety
        return True

    def is_runtime_bigint(self, expr: TpyExpr, expr_type: TpyType) -> bool:
        """Check if expression is stored as BigInt at runtime."""
        resolved = self.get_resolved_type(expr)
        if is_big_int_type(resolved):
            return True
        if isinstance(resolved, IntLiteralType):
            resolved_default = self.ctx.analyzer.ctx.default_int_for_literal(resolved)
            return is_big_int_type(resolved_default)
        return False

    def varargs_elem_cpp(self, elem: TpyType) -> str:
        """C++ element type for a `*args` parameter's `varargs<...>`."""
        return varargs_elem_cpp(elem, self.type_to_cpp)

    def type_to_cpp(self, typ: TpyType) -> str:
        """Convert a type to its C++ representation, qualifying imported types.

        For imported record types from user modules, generates fully qualified names
        like tpyapp::utils::Point or tpyapp::pkg::mod::Point for packages.
        Native records use their native C++ name directly (no namespace qualification).
        @dynamic protocol types map to the base class name.
        """
        # A bound coroutine or generator object renders as its concrete
        # frame struct (`__coro_*` / `__gen_*`), NOT the protocol it
        # subtypes -- must precede the protocol branch below.
        # Own[ConcreteCoro] is the storage form: std::optional<frame>.
        if isinstance(typ, ConcreteFrameType):
            return rcfg.frame_struct_qualname(
                self, typ.frame_owner, typ.frame_func_name,
                typ.frame_inferred_type_args,
                module_qual=typ.frame_module_qual,
                shape=(rcfg.ResumableShape.ASYNC
                       if isinstance(typ, ConcreteCoroType)
                       else rcfg.ResumableShape.GENERATOR))
        if isinstance(typ, OwnType):
            own_inner = unwrap_readonly(typ.wrapped)
            if isinstance(own_inner, ConcreteCoroType):
                return f"std::optional<{self.type_to_cpp(own_inner)}>"
        if isinstance(typ, NominalType) and is_protocol_type(typ):
            protocol_info = protocol_info_of(typ)
            if protocol_info and protocol_info.is_dynamic:
                return self.protocols.get_dynamic_base_name(typ)
        if isinstance(typ, NominalType) and typ.is_user_record:
            # Native records use their native C++ name directly (globally visible)
            record_info = self.ctx.analyzer.registry.get_record_for_type(typ)
            if record_info and record_info.is_native:
                return typ.to_cpp()  # to_cpp() already resolves via Compiler.native_cpp_names
            # Cross-module user record: qualify to the declaring module (canonical identity).
            # Use the type-aware variant so records reachable only via an inferred
            # cross-module return type (e.g. `import mod; p = mod.f()` where
            # `f() -> Own[Pattern]`) still qualify -- short-name lookup would
            # miss because `Pattern` isn't in the caller's local registry.
            qual = self.ctx.analyzer.registry.imported_record_qualification_for_type(
                typ, self.ctx.analyzer.ctx.module_name)
            if qual is not None:
                source_module, original_name = qual
                qualified = qualified_cpp_name(source_module, original_name)
                if typ.type_args:
                    args = ", ".join(
                        self.type_to_cpp(t) if isinstance(t, TpyType) else str(t)
                        for t in typ.type_args
                    )
                    return f"{qualified}<{args}>"
                return qualified
        # Enum type-position spelling. Routes through enum_cpp_name so
        # @native enums render as their user-supplied qname rather than
        # tpyapp::<module>::E. For local non-native enums this falls
        # through to the local-name return below.
        if is_enum_type(typ):
            cur_module = self.ctx.analyzer.ctx.module_name
            spelled = enum_cpp_name(typ, cur_module)
            # Only return early if the helper produced something other
            # than the bare local name (otherwise let the normal type
            # rendering path continue, which uses Compiler.native_cpp_names).
            if spelled != typ.name:
                return spelled
        # Tuple types: qualify element types for imported members
        if isinstance(typ, TupleType):
            args = ", ".join(self.type_to_cpp(t) for t in typ.element_types)
            return f"std::tuple<{args}>"
        # Union types: use alias name if registered, otherwise qualify member names
        if isinstance(typ, UnionType):
            compiler = get_current_compiler()
            alias = compiler.union_alias_names.get(typ.members) if compiler is not None else None
            if alias is not None:
                return alias
            cpp_members = [
                "std::monostate" if is_void_like_type(m) else self.type_to_cpp(m)
                for m in typ.members
            ]
            # Mirrors UnionType.to_cpp: one head at every position, the
            # type that owns Python's comparison rule; the ALTERNATIVES say
            # which form this is.
            return f"::tpy::Union<{', '.join(cpp_members)}>"
        # Resolve PendingViewType to concrete types before codegen
        if isinstance(typ, PendingViewType):
            return self._resolve_pending_view(typ).to_cpp()
        # For plain NominalType (not subclasses like ListType/ArrayType) with
        # type_args, recursively resolve args to handle @dynamic protocols.
        # Skip this branch when the TypeDef registry provides a custom
        # cpp_formatter (e.g. CopyIter/OwnIter -> "auto") -- fall through to
        # typ.to_cpp() so the formatter wins.
        if type(typ) is NominalType and typ.type_args:
            from tpyc.type_def_registry import type_def_of
            td = type_def_of(typ)
            if td is None or td.cpp_formatter is None:
                base = typ.to_cpp_base_name()
                const_readonly = spells_readonly_arg_const(td)
                args = ", ".join(
                    template_arg_cpp(t, const_readonly, self.type_to_cpp)
                    for t in typ.type_args
                )
                return f"{base}<{args}>"
        # Default: use the type's built-in to_cpp() method
        return typ.to_cpp()

    def typed_brace_init(self, init_expr: str, target_type: TpyType | None) -> str:
        """Prefix a brace-init with its destination C++ type (`T{...}`) so it
        can bind to a forwarding-ref / template parameter that cannot deduce a
        bare brace-init-list (`__setitem__`, `ordered_map::insert_or_assign`).
        No-op for expressions already self-describing (not starting with `{`).
        """
        if not init_expr.startswith("{") or target_type is None:
            return init_expr
        unwrapped = unwrap_readonly(unwrap_ref_type(target_type))
        return f"{self.type_to_cpp(unwrapped)}{init_expr}"

    def type_to_cpp_stored(self, typ: TpyType) -> str:
        """Convert a type to its stored C++ representation.

        Like type_to_cpp but uses to_cpp_stored() (val_or_ref<T> for Ref types).
        Resolves PendingViewType before conversion.
        """
        if isinstance(typ, PendingViewType):
            return self._resolve_pending_view(typ).to_cpp_stored()
        return typ.to_cpp_stored()

    def type_to_cpp_ptr_variant(self, typ: 'UnionType') -> str:
        """Return the borrow-form type with qualified member names.

        For non-value unions: ::tpy::Union<Dog*, Cat*> with cross-module
        qualification on member types. Monostate members pass through.
        """
        cpp_members = [
            "std::monostate" if is_void_like_type(m)
            else f"{self.type_to_cpp(m)}*"
            for m in typ.members
        ]
        return f"::tpy::Union<{', '.join(cpp_members)}>"

    def type_to_cpp_const_ptr_variant(self, typ: 'UnionType') -> str:
        """Return the read-borrow form with qualified member names."""
        cpp_members = [
            "std::monostate" if is_void_like_type(m)
            else f"const {self.type_to_cpp(m)}*"
            for m in typ.members
        ]
        return f"::tpy::Union<{', '.join(cpp_members)}>"

