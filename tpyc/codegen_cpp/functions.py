"""
TurboPython Function Code Generation

Generates C++ function declarations, definitions, and global variables.
"""

from __future__ import annotations
from typing import Literal, TextIO, TYPE_CHECKING

# Method body placement when emitting from `gen_method_def` and friends.
# - "inline": signature + body, indented, inside the struct definition (the
#   default; required for templates and methods that emit a C++ template
#   header).
# - "decl": signature + ``;``, indented, inside the struct.
# - "def_hpp": ``inline ReturnType ClassName::method(...) { body }`` at
#   namespace scope in the .hpp, after every struct decl is complete. Used
#   for trivial methods so the call site can inline without LTO.
# - "def_cpp": ``ReturnType ClassName::method(...) { body }`` at namespace
#   scope in the .cpp. No ``inline`` keyword (single TU owns the symbol).
#   Used for non-trivial method bodies; consistent with how free functions
#   are emitted.
MethodEmitMode = Literal["inline", "decl", "def_hpp", "def_cpp"]

from ..typesys import (
    default_emittable_at, none_default_cpp_spelling,
    TpyType, NominalType, OwnType, ReadonlyType, OptionalType, PendingListType, IntLiteralType, is_fn_type, CallableType,
    UnionType, VoidType, NoneType, NONE,
    BIGINT, BOOL, STR, is_protocol_type, is_protocol_union, is_dyn_protocol, FunctionInfo, TypeParamRef, unwrap_readonly, is_constexpr_eligible,
    PtrType, LiteralType, LiteralValue, LiteralTag, is_any_str_type,
    is_primitive_type,
    resolve_int_literals, CONST_PARAMS_METHODS,
    error_return_to_cpp, error_return_uses_borrow_slot, unwrap_ref_type,
    property_getter_returns_storage_ref,
    bare_name, recorded_return_borrow_sources, return_const_projected,
    body_function_info, body_method_info,
)
from ..parse import TpyFunction, TpyVarDecl, VarLinkage
from ..type_def_registry import is_varargs, is_char_type, is_str_type, is_bytes_type, is_bytes_view_type, protocol_info_of
from ..parse.nodes import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral,
    TpyBytesLiteral, TpyNoneLiteral, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyName, TpyFieldAccess, TpyStmt, OverloadForm,
)
from .context import INDENT, module_to_cpp_namespace, escape_cpp_name, qualified_cpp_name, cpp_string_literal_expr, cpp_bytes_literal_span, cpp_bytes_literal_owned, expand_cpp_template, enum_member_cpp, CodeGenError
from . import emit_prims
from .param_const import decide_param_const, ParamConstDecision
from .forms import is_borrow_form_tuple_global
from .type_resolution import (resolve_global_binding_type,
                              resolve_stmt_type_cascade)
from .int_literals import render_int_literal_value
from ..sema.literal_utils import literal_value_from_expr

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .protocols import ProtocolGenerator, ProtocolParamInfo


def _infer_literal_default_type(expr: TpyExpr) -> TpyType | None:
    """Best-effort concrete type for a default expression, for overload narrowing.

    Returns the narrowest literal/none type when the default is a simple
    literal (None, int, float, bool, str); None for more complex defaults.
    Used only to compute dead-branch-elim hints for short-arity overload stubs;
    unrecognized defaults simply skip narrowing and the body compiles as-is.
    """
    if isinstance(expr, TpyNoneLiteral):
        return NONE
    if isinstance(expr, TpyIntLiteral):
        return IntLiteralType(value=expr.value)
    if isinstance(expr, TpyBoolLiteral):
        return LiteralType(BOOL, (LiteralValue(LiteralTag.BOOL, expr.value),))
    if isinstance(expr, TpyStrLiteral):
        return LiteralType(STR, (LiteralValue(LiteralTag.STR, expr.value),))
    if isinstance(expr, TpyUnaryOp) and expr.op == "-":
        inner = _infer_literal_default_type(expr.operand)
        if isinstance(inner, IntLiteralType) and inner.value is not None:
            return IntLiteralType(value=-inner.value)
    return None


def default_emittable(defaults: list, i: int, n_params: int,
                      ptype: 'TpyType | None' = None,
                      params: 'list | None' = None,
                      is_member: bool = False) -> bool:
    """Can parameter `i`'s default be a C++ default argument? Thin adapter over
    `default_emittable_at`, which is the one definition the call-site fill is
    the complement of."""
    slots = [(t, defaults[j] if j < len(defaults) else None)
             for j, (_n, t) in enumerate(params or [])] if params else [
        (ptype, defaults[j] if j < len(defaults) else None)
        for j in range(n_params)]
    return default_emittable_at(slots, i, is_member) if i < len(slots) else False


def _narrow_overload_param(impl_ptype: TpyType, concrete: TpyType) -> TpyType | None:
    """Return concrete when it tightens impl_ptype for dead-branch elim."""
    if isinstance(impl_ptype, UnionType) and not isinstance(concrete, UnionType):
        return concrete
    if isinstance(impl_ptype, OptionalType) and not isinstance(concrete, OptionalType):
        # Concrete is either the inner T, NoneType, or a Literal of the inner.
        if isinstance(concrete, NoneType):
            return concrete
        if isinstance(concrete, LiteralType):
            return concrete
        if concrete == impl_ptype.inner:
            return concrete
        return None
    if isinstance(concrete, (LiteralType, IntLiteralType)) and not isinstance(impl_ptype, (LiteralType, IntLiteralType)):
        return concrete
    return None


def build_overload_narrowing(
    impl: TpyFunction, stub: TpyFunction,
    missing_params: list[tuple[str, TpyType]],
    impl_defaults: list,
) -> dict[str, TpyType]:
    """Build the overload_param_types map for dead-branch elim in a spec.

    Shared by the per-stub signature specializer and THIR's per-stub lowering,
    so both resolve the same facts from one map.

    - Stub-shadowed union impl params narrow to the stub's concrete member.
    - Stub-shadowed Optional impl params narrow to the stub's non-Optional
      type (inner T or NoneType).
    - Stub-shadowed non-union impl params narrow to a LiteralType stub type.
    - Missing impl params narrow to the default expression's concrete type
      where that gives information beyond the declared impl param type.
    """
    narrowing: dict[str, TpyType] = {}
    for (impl_pname, impl_ptype), (stub_pname, stub_ptype) in zip(impl.params, stub.params):
        narrow = _narrow_overload_param(impl_ptype, stub_ptype)
        if narrow is not None:
            narrowing[stub_pname] = narrow
    for i, (pname, ptype) in enumerate(missing_params,
                                       start=len(impl.params) - len(missing_params)):
        default_expr = impl_defaults[i] if i < len(impl_defaults) else None
        if default_expr is None:
            continue
        default_type = _infer_literal_default_type(default_expr)
        if default_type is None:
            continue
        narrow = _narrow_overload_param(ptype, default_type)
        if narrow is not None:
            narrowing[pname] = narrow
    return narrowing


def overload_stubs_are_literal_only(
    stubs: 'list', impl: TpyFunction,
) -> bool:
    """Check if overload stubs differ from the impl only by Literal annotations.

    When all stubs have the same C++ parameter types as the implementation
    (because they only differ by LiteralType vs base type), per-stub
    specialization would produce duplicate C++ definitions. In that case,
    emit just the implementation function. Shared with THIR's per-stub gate
    (a literal-only group emits through the mangled-name path, which never
    sets the per-stub interception key).
    """
    for stub in stubs:
        for (_, impl_ptype), (_, stub_ptype) in zip(impl.params, stub.params):
            if isinstance(stub_ptype, LiteralType):
                continue
            if stub_ptype != impl_ptype:
                return False
    return any(
        isinstance(ptype, LiteralType)
        for stub in stubs
        for _, ptype in stub.params
    )


def literal_mangled_name(base_name: str, stub_or_info: TpyFunction | FunctionInfo) -> str:
    """Generate a mangled C++ name for a literal-specialized function.

    Collects all LiteralType values from stub params and appends them
    as __lit_{sanitized}. Multi-value stubs join with double underscore.
    E.g. Literal["age"] -> __lit_age, Literal[1, 2] -> __lit_1__2,
    Literal[-1] -> __lit_neg1. Accepts TpyFunction (params as tuples)
    or FunctionInfo (params as ParamInfo objects).
    """
    parts: list[str] = []
    params = stub_or_info.params
    for p in params:
        ptype = p[1] if isinstance(p, tuple) else p.type
        if isinstance(ptype, LiteralType):
            for v in ptype.values:
                s = str(v.value)
                if s.startswith("-"):
                    s = "neg" + s[1:]
                sanitized = "".join(c if c.isalnum() else "_" for c in s)
                parts.append(sanitized)
    if not parts:
        return base_name
    return f"{base_name}__lit_{'__'.join(parts)}"


# Zero-arg scalar constructors that codegen to `0` (as opposed to
# aggregate init `{}`). Covers every fixed-int width plus the three
# bare numeric/bool builtins -- misnamed `_FIXED_INT_NAMES` historically.
_SCALAR_ZERO_CTOR_NAMES = {
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
    "int", "float", "bool",
}


# A `None` into a structural-protocol parameter, which is a template: the
# argument names the `T_x = std::nullptr_t` instantiation.
NULL_PROTOCOL_ARG_CPP = "static_cast<std::nullptr_t*>(nullptr)"


def factory_default_to_cpp(field_type: TpyType) -> str:
    """Return the C++ default value for a factory-default field.

    User records need explicit ctor call (their ctors are explicit);
    containers (list, dict, etc.) use aggregate init.
    """
    inner = field_type.wrapped if isinstance(field_type, OwnType) else field_type
    if isinstance(inner, NominalType) and inner.is_user_record:
        return f"{inner.to_cpp()}()"
    return "{}"


def default_to_cpp(ctx: 'CodeGenContext', expr: TpyExpr, ptype: TpyType) -> str:
    """Convert a constant default expression to its C++ representation."""
    return default_to_cpp_from_analyzer(ctx.analyzer, expr, ptype)


def default_to_cpp_from_analyzer(analyzer, expr: TpyExpr,
                                 ptype: TpyType) -> str:
    """`default_to_cpp` keyed on the analyzer directly -- the slice of ctx
    it reads -- so the THIR per-stub prologue can share the renderer."""
    if isinstance(expr, TpyTypeParamConstruct):
        return f"{expr.param_name}{{}}"
    lit = literal_value_from_expr(expr)
    if lit is not None and lit.tag is LiteralTag.INT:
        # Route int-literal defaults (bare or unary-minus) through the shared
        # renderer so a >int32 value pins its C++ width -- a bare `long` token
        # converts to BigInt ambiguously on macOS. Before the generic
        # unary-minus arm below, so a negated literal renders from its signed
        # value (not a `-` prefixed onto the positive token's width).
        return render_int_literal_value(
            lit.value, ptype,
            default_int_type=analyzer.ctx.default_int_type,
            type_to_cpp=lambda t: t.to_cpp())
    if isinstance(expr, TpyFloatLiteral):
        v = repr(expr.value)
        if '.' not in v and 'e' not in v and 'E' not in v:
            v += '.0'
        return v
    if isinstance(expr, TpyBoolLiteral):
        return "true" if expr.value else "false"
    if isinstance(expr, TpyStrLiteral):
        if is_char_type(ptype) and len(expr.value) == 1:
            ch = expr.value[0]
            if ch == "'":
                return "'\\''"
            if ch == '\\':
                return "'\\\\'"
            return f"'{ch}'"
        return cpp_string_literal_expr(expr.value)
    if isinstance(expr, TpyBytesLiteral):
        if not expr.value:
            return "{}"
        # bytes/BytesView params both lower to ::tpy::BytesView; pinning
        # the literal to static storage avoids a per-call vector allocation.
        if is_bytes_view_type(ptype) or is_bytes_type(ptype):
            return cpp_bytes_literal_span(expr.value)
        return cpp_bytes_literal_owned(expr.value)
    if isinstance(expr, TpyNoneLiteral):
        return none_default_cpp_spelling(ptype)
    if isinstance(expr, TpyName):
        # Final[T] module constant in default position (sema validated).
        # For imported names: route through the attribute table's
        # ultimate definer (bypasses parent-package `inline auto&`
        # aliases that may not be visible in the sibling-cross-import +
        # Final-reexport shape, where parent.hpp is pulled into a
        # sub.hpp via the auto-parent walk before the sub's namespace
        # has opened). For locally-defined Final globals we still emit
        # the bare name -- `lookup_qualified` would resolve them to
        # `current_module` which is `__main__` for the entry module,
        # mismatching the actual C++ namespace `tpyapp::main`.
        table = analyzer.ctx.module_attributes
        cell = table.get(expr.name) if table else None
        bd = cell.binding if cell is not None else None
        if bd is not None and bd.defining_module is not None:
            return qualified_cpp_name(bd.defining_module, bd.canonical_name)
        imp = analyzer.imported_names.get(expr.name)
        if imp is not None:
            source_module, original_name = imp
            return qualified_cpp_name(source_module, original_name)
        return escape_cpp_name(expr.name)
    if isinstance(expr, TpyFieldAccess) and isinstance(expr.obj, TpyName):
        # Enum-member default: MemoryOrder.SEQ_CST -> the scoped enumerator.
        # get_enum resolves both local and imported enums (parser accepts the
        # Name.attr shape without type info, so a non-enum reaching here is a
        # user error, not an internal one).
        enum_type = analyzer.registry.get_enum(expr.obj.name)
        if enum_type is not None:
            return enum_member_cpp(enum_type, analyzer.ctx.module_name, expr.field)
        raise CodeGenError(
            f"default value '{expr.obj.name}.{expr.field}' is not a resolvable "
            f"enum member", expr.loc)
    if isinstance(expr, TpyUnaryOp) and expr.op == "-":
        return f"-{default_to_cpp_from_analyzer(analyzer, expr.operand, ptype)}"
    if isinstance(expr, TpyCall):
        # int32(5) -> just the literal value
        if expr.args:
            return default_to_cpp_from_analyzer(analyzer, expr.args[0], ptype)
        # Zero-arg call: int32() -> 0, list()/dict()/Record() -> {}
        if isinstance(expr.func, TpyName) and expr.func_name in _SCALAR_ZERO_CTOR_NAMES:
            return "0"
        return factory_default_to_cpp(ptype)
    return "0"


class FunctionGenerator:
    """Generates C++ functions, methods, and globals."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        protocols: ProtocolGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.protocols = protocols

    def _collect_fn_params(self, params: list[tuple[str, TpyType]]) -> list[tuple[int, str, CallableType]]:
        """Collect Fn-typed parameters: returns (fn_index, param_name, fn_type)."""
        result = []
        idx = 0
        for pname, ptype in params:
            if is_fn_type(ptype):
                result.append((idx, pname, ptype))
                idx += 1
        return result

    def _gen_fn_template_parts(
        self, fn_params: list[tuple[int, str, CallableType]],
    ) -> tuple[list[str], list[str]]:
        """Generate template params and requires clauses for Fn-typed params.

        Returns (template_parts, requires_parts).
        """
        template_parts = []
        requires_parts = []
        for idx, pname, fn_type in fn_params:
            template_parts.append(f"typename __F{idx}")
            # Build requires clause: { __fn(__a0, __a1, ...) } -> std::convertible_to<R>
            req_params_list = [f"__F{idx}& __fn"] + [
                f"{pt.to_cpp_param_type()} __a{i}" for i, pt in enumerate(fn_type.param_types)
            ]
            call_args = ", ".join(f"__a{i}" for i in range(len(fn_type.param_types)))
            if isinstance(fn_type.return_type, VoidType):
                requires_parts.append(
                    f"requires({', '.join(req_params_list)}) {{\n"
                    f"    __fn({call_args});\n"
                    f"}}"
                )
            else:
                ret_cpp = fn_type.return_type.to_cpp()
                requires_parts.append(
                    f"requires({', '.join(req_params_list)}) {{\n"
                    f"    {{ __fn({call_args}) }}"
                    f" -> std::convertible_to<{ret_cpp}>;\n"
                    f"}}"
                )
        return template_parts, requires_parts

    @staticmethod
    def _merge_fn_into_header(
        base_header: str,
        fn_tpl_parts: list[str],
        fn_req_parts: list[str],
        indent: str = "",
    ) -> str:
        """Merge Fn template params/requires into an existing (or empty) template header.

        indent: prefix for continuation lines (e.g. INDENT for method context).
        """
        def _indent_requires(req_str: str) -> str:
            """Indent all lines of a requires clause."""
            lines = req_str.split("\n")
            return "\n".join(f"{indent}  {line}" for line in lines)

        if base_header:
            header_lines = base_header.rstrip("\n").split("\n")
            tpl_line = header_lines[0].strip()
            inner = tpl_line[len("template<"):-1]
            all_parts = inner + ", " + ", ".join(fn_tpl_parts) if inner else ", ".join(fn_tpl_parts)
            new_tpl = f"template<{all_parts}>"

            existing_requires = ""
            if len(header_lines) > 1 and header_lines[1].strip().startswith("requires"):
                existing_requires = header_lines[1].strip()
            if existing_requires and fn_req_parts:
                fn_clause = _indent_requires(" && ".join(fn_req_parts))
                return f"{new_tpl}\n{indent}  {existing_requires} &&\n{fn_clause}\n"
            elif fn_req_parts:
                fn_clause = _indent_requires("requires " + " && ".join(fn_req_parts))
                return f"{new_tpl}\n{fn_clause}\n"
            elif existing_requires:
                return f"{new_tpl}\n{indent}  {existing_requires}\n"
            return f"{new_tpl}\n"
        else:
            tpl = f"template<{', '.join(fn_tpl_parts)}>"
            if fn_req_parts:
                fn_clause = _indent_requires("requires " + " && ".join(fn_req_parts))
                return f"{tpl}\n{fn_clause}\n"
            return f"{tpl}\n"

    def _gen_template_header_with_fn(
        self, func: TpyFunction,
        proto_params: list,
        *, emit_defaults: bool = True,
        indent: str = "",
    ) -> str:
        """Generate a template header that includes both protocol and Fn params.

        indent: prefix for continuation lines (e.g. INDENT for method context).
        """
        fn_params = self._collect_fn_params(func.params)
        base_header = self.protocols.gen_combined_template_header(
            func.type_params, proto_params, func.type_param_bounds,
            emit_defaults=emit_defaults,
        )
        if not fn_params:
            return base_header

        fn_tpl_parts, fn_req_parts = self._gen_fn_template_parts(fn_params)
        return self._merge_fn_into_header(base_header, fn_tpl_parts, fn_req_parts, indent=indent)

    def gen_params(self, params: list[tuple[str, TpyType]],
                   func_type_params: list[str] | None = None,
                   *, const_params: bool = False,
                   reassigned_params: set[str] | None = None,
                   mutated_params: frozenset[int] | None = None,
                   addr_escapes_params: frozenset[int] = frozenset(),
                   defaults: list | None = None,
                   emit_defaults: bool = False,
                   class_type_params: set[str] | None = None,
                   func: 'TpyFunction | None' = None,
                   is_member: bool = False) -> str:
        """Generate function parameter list.

        Own[T] params are emitted as T&& (rvalue ref) for concrete T, or
        std::type_identity_t<T>&& for template T (prevents forwarding-ref
        deduction). Callers always pass std::move() at last use; non-last-use
        copies are inserted at the call site with a warning.

        const_params: use to_cpp_const_param (const T& for generics). Needed
        for constructors and const method overloads that must accept temporaries.

        reassigned_params: params that are reassigned in the function body.
        Types normally passed as const ref (BigInt, str) get a renamed C++
        param (__param_X) so the body can shadow it with a mutable local copy.

        mutated_params: frozenset of param indices with confirmed mutations.
        When provided, non-mutated params that are not reassigned get const T&
        instead of T& (read-only binding). None = conservative (T& for all).

        defaults: list of TpyExpr | None aligned with params.
        emit_defaults: if True, append ' = <value>' for params with defaults.
        """
        parts = []
        fn_idx = 0
        member = is_member or bool(func and func.is_method)
        for i, (pname, ptype) in enumerate(params):
            cpp_pname = escape_cpp_name(pname)
            # Fn params become forwarding-ref template params
            if is_fn_type(ptype):
                part = f"__F{fn_idx}&& {cpp_pname}"
                fn_idx += 1
                if emit_defaults and defaults and default_emittable(defaults, i, len(params), ptype, params, member):
                    part += f" = {default_to_cpp(self.ctx, defaults[i], ptype)}"
                parts.append(part)
                continue
            own = unwrap_readonly(unwrap_ref_type(ptype))
            bare_ptype = unwrap_ref_type(ptype)
            # *args parameter: emit tpy::varargs<T> (varargs<const T> for a
            # readonly vararg -- the element readonly-ness drives const access).
            if func and func.vararg_name and pname == func.vararg_name and is_varargs(bare_ptype):
                inner_cpp = self.types.varargs_elem_cpp(bare_ptype.type_args[0])
                part = f"::tpy::varargs<{inner_cpp}> {escape_cpp_name(pname)}"
                if emit_defaults and defaults and default_emittable(defaults, i, len(params), ptype, params, member):
                    part += f" = {default_to_cpp(self.ctx, defaults[i], ptype)}"
                parts.append(part)
                continue
            if (reassigned_params and pname in reassigned_params
                    and ptype.param_needs_copy_for_reassign()):
                # Rename param so the body can declare a mutable local with the original name
                part = ptype.to_cpp_param(f"__param_{cpp_pname}")
            else:
                is_pvu = self.ctx.is_ptr_variant_union(own)
                decision = decide_param_const(
                    ptype,
                    index=i,
                    pname=pname,
                    mutated_params=mutated_params,
                    addr_escapes_params=addr_escapes_params,
                    reassigned_params=reassigned_params,
                    is_ptr_variant_union=is_pvu,
                    const_params=const_params,
                )
                part = self._emit_param_with_decision(
                    decision, ptype, cpp_pname)
            # Own[T] where T is a class-level type param: std::type_identity_t is
            # redundant (T is already bound, T&& is a plain rvalue ref, not forwarding).
            # Only function-level type params need the deduction guard.
            if (isinstance(ptype, OwnType) and isinstance(ptype.wrapped, TypeParamRef)
                    and class_type_params is not None
                    and ptype.wrapped.name in class_type_params):
                tp_cpp = ptype.wrapped.to_cpp()
                part = part.replace(f"std::type_identity_t<{tp_cpp}>&&", f"{tp_cpp}&&")
            if emit_defaults and defaults and default_emittable(defaults, i, len(params), ptype, params, member):
                part += f" = {default_to_cpp(self.ctx, defaults[i], ptype)}"
            parts.append(part)
        return ", ".join(parts)

    def gen_c_params(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate parameter list for extern \"C\" declarations.

        Uses C-compatible types: str maps to const char* instead of
        std::string_view (which is not ABI-compatible with C).
        """
        parts = []
        for pname, ptype in params:
            cpp_pname = escape_cpp_name(pname)
            if is_any_str_type(ptype):
                parts.append(f"const char* {cpp_pname}")
            else:
                parts.append(ptype.to_cpp_param(cpp_pname))
        return ", ".join(parts)

    def gen_params_with_protocols(self, params: list[tuple[str, TpyType]],
                                   func_type_params: list[str] | None = None,
                                   *, const_params: bool = False,
                                   mutated_params: frozenset[int] | None = None,
                                   defaults: list | None = None,
                                   emit_defaults: bool = False,
                                   func: 'TpyFunction | None' = None,
                                   is_member: bool = False) -> str:
        """Generate function parameter list, using template types for protocol params.

        Static protocols use template types (T_paramname).
        @dynamic protocols use concrete base class& reference params.
        Nullable protocol params emit const T_paramname* (pointer repr).
        const_params: emit const T_x& for static protocols, const Base& for @dynamic,
        and to_cpp_const_param for non-protocol params.
        mutated_params: frozenset of param indices with confirmed mutations (same
        semantics as gen_params). When provided, non-mutated protocol params use
        const T_x& / const Base& instead of T_x& / Base&.

        `func` carries the same member fallback as gen_params: a default's C++
        legality depends on whether the declaration sits inside a record, so a
        caller that knows the callable must not have to remember to say so twice.
        """
        result = []
        fn_idx = 0
        member = is_member or bool(func and func.is_method)
        for i, (pname, ptype) in enumerate(params):
            cpp_pname = escape_cpp_name(pname)
            # Fn params become forwarding-ref template params (same as gen_params)
            if is_fn_type(ptype):
                part = f"__F{fn_idx}&& {cpp_pname}"
                fn_idx += 1
                if emit_defaults and defaults and default_emittable(defaults, i, len(params), ptype, params, member):
                    part += f" = {default_to_cpp(self.ctx, defaults[i], ptype)}"
                result.append(part)
                continue
            unwrapped = unwrap_readonly(unwrap_ref_type(ptype))
            if self.protocols.is_static_protocol_param(ptype):
                # Unified static protocol handling (single, optional, or union)
                info = self._find_protocol_param_info(pname, ptype)
                # Own[Protocol] or Iterable[Own[T]] params use forwarding ref
                # (T&&) to accept rvalue move-only / consuming types.
                is_own_protocol = isinstance(unwrapped, OwnType)
                has_own_arg = (isinstance(unwrapped, NominalType) and unwrapped.type_args
                               and any(isinstance(a, OwnType) for a in unwrapped.type_args))
                if info and info.has_none:
                    part = f"const T_{pname}* {cpp_pname}"
                elif is_own_protocol or has_own_arg:
                    part = f"T_{pname}&& {cpp_pname}"
                elif (const_params or isinstance(ptype, ReadonlyType)
                        or self._all_protocols_readonly(info)
                        or (mutated_params is not None and i not in mutated_params)):
                    part = f"const T_{pname}& {cpp_pname}"
                else:
                    part = f"T_{pname}& {cpp_pname}"
            else:
                # Check for @dynamic protocol
                inner_unwrapped = unwrapped.inner if isinstance(unwrapped, OptionalType) else unwrapped
                resolved = self.protocols.resolve_type_for_codegen(inner_unwrapped)
                if (is_protocol_type(resolved)
                        and (pi := protocol_info_of(resolved))
                        and pi.is_dynamic):
                    base_type = self.protocols.get_dynamic_base_name(resolved)
                    is_const = (isinstance(ptype, ReadonlyType)
                                or (mutated_params is not None and i not in mutated_params))
                    const_kw = "const " if is_const else ""
                    if isinstance(unwrapped, OptionalType):
                        # Nullable @dynamic protocol param needs pointer-repr (a
                        # reference can't be null), mirroring Optional[concrete
                        # polymorphic root].
                        part = f"{const_kw}{base_type}* {cpp_pname}"
                    else:
                        part = f"{const_kw}{base_type}& {cpp_pname}"
                else:
                    own = unwrap_readonly(ptype)
                    is_pvu = self.ctx.is_ptr_variant_union(own)
                    decision = decide_param_const(
                        ptype,
                        index=i,
                        pname=pname,
                        mutated_params=mutated_params,
                        is_ptr_variant_union=is_pvu,
                        const_params=const_params,
                    )
                    part = self._emit_param_with_decision(decision, ptype, cpp_pname)
            if emit_defaults and defaults and default_emittable(defaults, i, len(params), ptype, params, member):
                part += f" = {default_to_cpp(self.ctx, defaults[i], ptype)}"
            result.append(part)
        return ", ".join(result)

    def _find_protocol_param_info(self, pname: str, ptype: TpyType):
        """Find ProtocolParamInfo for a single param (helper for gen_params_with_protocols)."""
        infos = self.protocols.get_all_protocol_params([(pname, ptype)])
        return infos[0] if infos else None

    def _all_protocols_readonly(self, info: ProtocolParamInfo | None) -> bool:
        """Check if all protocols in a ProtocolParamInfo are functionally const."""
        if info is None or not info.protocols:
            return False
        return all(self.protocols.is_protocol_const(p.name) for p in info.protocols)

    def _resolve_return_type(self, return_type: TpyType, *, const: bool = False,
                             error_return: str | None = None) -> str:
        """Map a return type to C++, using Base& for @dynamic protocols.

        If error_return is set, wraps the return type in std::expected<T, E>.
        """
        cpp_error = error_return_to_cpp(error_return, self.ctx.analyzer.ctx.module_name, self.ctx.analyzer.registry) if error_return else None
        unwrapped = unwrap_readonly(unwrap_ref_type(return_type))
        # Unwrap Own[Protocol] so consuming __iter__ returning Own[Iterator[T]]
        # gets `auto` in C++. Abstract @dynamic P must stay wrapped -- Own[P]
        # lowers to std::unique_ptr<P> via OwnType.to_cpp(); the protocol path
        # below would emit `P&` to a temporary.
        if (isinstance(unwrapped, OwnType) and is_protocol_type(unwrapped.wrapped)
                and not is_dyn_protocol(unwrapped.wrapped)):
            unwrapped = unwrapped.wrapped
        if is_protocol_type(unwrapped) and isinstance(unwrapped, NominalType):
            pi = protocol_info_of(unwrapped)
            if pi and pi.is_dynamic:
                base = self.protocols.get_dynamic_base_name(unwrapped)
                if const or isinstance(unwrap_ref_type(return_type), ReadonlyType):
                    ret = f"const {base}&"
                else:
                    ret = f"{base}&"
                if cpp_error:
                    if const or isinstance(unwrap_ref_type(return_type), ReadonlyType):
                        inner = f"::tpy::val_or_ref<const {base}>"
                    else:
                        inner = f"::tpy::val_or_ref<{base}>"
                    return f"std::expected<{inner}, {cpp_error}>"
                return ret
            # Non-dynamic protocol return (e.g. Iterator[T] from __iter__):
            # use auto, C++ deduces the type from the return expression.
            return "auto"
        unwrapped_return = unwrap_ref_type(unwrap_readonly(return_type))
        if const:
            ret = return_type.to_cpp_return_const()
        else:
            ret = return_type.to_cpp_return()
        if cpp_error:
            # std::expected can't hold references. For reference-returned types
            # (where ret is T&) -- including recursive-union wrappers -- use
            # val_or_ref<T> which stores by pointer. A const / readonly return
            # keeps the const through the stored pointer (val_or_ref<const T>),
            # mirroring the @dynamic-protocol branch above.
            inner = unwrapped_return.to_cpp()
            if error_return_uses_borrow_slot(return_type):
                if const or isinstance(unwrap_ref_type(return_type), ReadonlyType):
                    inner = f"::tpy::val_or_ref<const {inner}>"
                else:
                    inner = f"::tpy::val_or_ref<{inner}>"
            return f"std::expected<{inner}, {cpp_error}>"
        return ret

    @staticmethod
    def _emit_param_with_decision(
        decision: ParamConstDecision,
        ptype: 'TpyType',
        cpp_pname: str,
    ) -> str:
        """Emit the C++ spelling of a param given its const decision.

        One spelling per verdict for every type: a pointer-variant union's
        const form is the const-pointee one, which `to_cpp_const_param`
        renders, so the family needs no branch of its own.
        """
        if not decision.signature_const:
            return ptype.to_cpp_param(cpp_pname)
        return ptype.to_cpp_const_param(cpp_pname)

    def _build_param_const_sets(
        self,
        params: list[tuple[str, 'TpyType']],
        mutated_params: 'frozenset[int] | None',
        reassigned_params: 'set[str] | None' = None,
        use_const_params: bool = False,
        addr_escapes_params: 'frozenset[int]' = frozenset(),
    ) -> tuple[set[str], set[str]]:
        """Compute (const_ref_params, deep_const_borrow_params) in one pass.

        - const_ref_params: param names emitted as `const T&` in C++. Body
          codegen uses this to emit explicit `const T&` / `T&` for
          element-borrow locals instead of `auto&`. Tracks ordinary
          T&-shaped params and ptr-variant unions; Optional and Tuple
          surfaces have their own semantics in deep_const_borrow_params.
        - deep_const_borrow_params: param names whose inner spelling is
          const everywhere it surfaces (Optional `const T*`, tuple slots
          `const T*` / `const T&`). Read by body codegen for unpacked
          locals, alias propagation, and call-site lowering.
        """
        const_ref: set[str] = set()
        deep: set[str] = set()
        for i, (pname, ptype) in enumerate(params):
            inner = unwrap_readonly(unwrap_ref_type(ptype))
            decision = decide_param_const(
                ptype,
                index=i,
                pname=pname,
                mutated_params=mutated_params,
                addr_escapes_params=addr_escapes_params,
                reassigned_params=reassigned_params,
                is_ptr_variant_union=self.ctx.is_ptr_variant_union(inner),
                const_params=use_const_params,
            )
            if decision.signature_const and (
                    inner.is_ref_param() or self.ctx.is_ptr_variant_union(inner)):
                const_ref.add(pname)
            if decision.deep_borrow_const:
                deep.add(pname)
        return const_ref, deep

    def compute_body_const_sets(
        self, func: TpyFunction, record_name: str | None
    ) -> tuple[set[str], set[str]]:
        """Compute (const_ref_params, deep_const_borrow_params) for a function
        or record method body."""
        rp = self._get_reassigned_params(func)
        if record_name is None:
            mp = self._get_func_mutated_params(func)
            ae = self._get_func_addr_escapes(func)
            use_const_params = func.is_readonly
        else:
            mp = self._get_method_mutated_params(func, record_name)
            ae = self._get_method_addr_escapes(func, record_name)
            # Inplace dunders take const params even though they mutate self;
            # auto_readonly_params_resolved means the parser clone already
            # marked params with ReadonlyType, so don't blanket-apply.
            use_const_params = ((func.is_readonly and not func.auto_readonly_params_resolved)
                                or func.name in CONST_PARAMS_METHODS)
        crp, dcbp = self._build_param_const_sets(
            func.params, mp, rp, use_const_params, addr_escapes_params=ae)
        # `_build_param_const_sets` only looks at `func.params`; `self` isn't
        # in there, so the readonly-self add lives at the caller.
        if func.is_readonly and record_name is not None:
            crp.add("self")
        return crp, dcbp

    def _get_reassigned_params(self, func: TpyFunction) -> set[str] | None:
        """Get the set of param names reassigned in the function body, or None."""
        scan = self.ctx.analyzer.function_scan_results.get(func)
        if not scan:
            return None
        param_names = {pname for pname, _ in func.params}
        result = scan.reassigned & param_names
        return result if result else None

    def _func_body_info(self, func: TpyFunction) -> FunctionInfo | None:
        """The FunctionInfo carrying `func`'s analysis facts. A bodyless
        @overload stub reads its implementation's (the group's positional
        pick); any other declaration-only stub (@native, a @dispatch binding)
        has none."""
        if func.is_stub and func.overload_form is not OverloadForm.OVERLOAD:
            return None
        return body_function_info(self.ctx.analyzer.registry, func)

    def _method_body_info(self, method: TpyFunction, record_name: str) -> FunctionInfo | None:
        """The FunctionInfo carrying `method`'s analysis facts; None for a
        declaration-only stub."""
        if method.is_stub:
            return None
        return body_method_info(self.ctx.analyzer.registry.get_record(record_name), method)

    def _get_func_mutated_params(self, func: TpyFunction) -> frozenset[int] | None:
        """Return finalized mutated_params for a free function, or None if unavailable."""
        fi = self._func_body_info(func)
        return fi.mutated_params if fi is not None else None

    def _get_func_addr_escapes(self, func: TpyFunction) -> frozenset[int]:
        """Return param indices whose address escapes via a mutable Ptr[T] field."""
        fi = self._func_body_info(func)
        return fi.addr_escapes_params if fi is not None else frozenset()

    def _get_method_mutated_params(self, method: TpyFunction, record_name: str) -> frozenset[int] | None:
        """Return finalized mutated_params for a record method, or None if unavailable."""
        fi = self._method_body_info(method, record_name)
        return fi.mutated_params if fi is not None else None

    def _get_method_addr_escapes(self, method: TpyFunction, record_name: str) -> frozenset[int]:
        """Return method param indices whose address escapes via a mutable Ptr[T] field."""
        fi = self._method_body_info(method, record_name)
        return fi.addr_escapes_params if fi is not None else frozenset()

    def _method_return_const_projected(self, method: TpyFunction, record_name: str) -> bool:
        """Whether a const method's borrowed return is const too."""
        fi = body_method_info(self.ctx.analyzer.registry.get_record(record_name), method)
        # Only an INFERRED verdict can split the two.
        return fi is None or not fi.root.readonly_inferred or return_const_projected(fi)

    def _get_method_genuine_mutated_params(self, method: TpyFunction, record_name: str) -> frozenset[int] | None:
        """Return mutation indices excluding return-borrow roots for const codegen.

        Borrow exposure shares the mutation set; its roots are subtracted
        even when also modified -- but only where the return is
        const-projected, since a mutable borrow of a parameter is a write
        path through it. Finalized facts retain unrelated transitive
        mutations so those parameters stay non-const.
        """
        fi = self._method_body_info(method, record_name)
        if fi is None or fi.mutated_params is None:
            return None
        mp = fi.mutated_params
        if fi.root.readonly_inferred and not return_const_projected(fi):
            return mp
        return mp - recorded_return_borrow_sources(fi)

    def _has_dynamic_protocol_params(self, params: list[tuple[str, TpyType]]) -> bool:
        """Check if any params are @dynamic protocol types (need Base& codegen)."""
        for _, ptype in params:
            unwrapped = unwrap_readonly(unwrap_ref_type(ptype))
            if isinstance(unwrapped, OptionalType):
                unwrapped = unwrapped.inner
            resolved = self.protocols.resolve_type_for_codegen(unwrapped)
            if is_protocol_type(resolved):
                protocol_info = protocol_info_of(resolved)
                if protocol_info and protocol_info.is_dynamic:
                    return True
        return False

    def _signature_is_template(self, func: TpyFunction) -> bool:
        """Per-signature template test -- the building block of the group-aware
        is_template_function, which also folds in an @overload group's stubs.
        THIR's per-stub admission mirrors only the PARAM half of this
        analyzer-purely (`_stub_has_template_param`, thir/lower/functions.py):
        declared type params are signature, which this path still prints, so
        THIR lowers those specializations' bodies."""
        if func.type_params:
            return True
        if self.protocols.get_all_protocol_params(func.params):
            return True
        if any(is_fn_type(pt) for _, pt in func.params):
            return True
        return False

    def is_template_function(self, func: TpyFunction) -> bool:
        """Check if a function emits any C++ template definition.

        For an @overload implementation the group is header-only if ANY stub is
        a template, even when the impl signature is not: a template stub must be
        emitted (and stay instantiable) in the header, else a cross-module call
        finds no instantiation. The header-vs-.cpp routing keys on this, so it
        must reflect the whole group, not just the impl signature."""
        if self._signature_is_template(func):
            return True
        stubs = self.ctx.analyzer.overload_groups.get(func)
        if stubs and any(self._signature_is_template(s) for s in stubs):
            return True
        return False

    def gen_function_forward_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate a function forward declaration (signature only, no body).

        Emitted before record definitions so that inline constructor/method
        bodies can call free functions declared later in the header.
        Returns True if a declaration was emitted.
        """
        from ..parse.nodes import FunctionLinkage
        if func.linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C,
                            FunctionLinkage.EXPORT_C):
            return False
        if func.cpp_template:
            return False
        # @builtin_function-keyed functions have sema/codegen handled
        # specially per call site; the stub function itself has no C++
        # representation (typing.cast, builtins.isinstance, ...).
        if func.builtin_function_key is not None:
            return False
        # @overload implementation: emit forward decls for each stub instead
        overload_stubs = self.ctx.analyzer.overload_groups.get(func)
        if overload_stubs:
            if self._overload_stubs_are_literal_only(overload_stubs, func):
                for stub in overload_stubs:
                    mangled = self._literal_mangled_name(func.name, stub)
                    self._gen_function_forward_decl_single(
                        out, func, cpp_name=mangled,
                        return_type_override=stub.return_type,
                    )
            else:
                for stub in overload_stubs:
                    self._gen_function_forward_decl_single(out, stub)
            return True
        if func.is_stub and not func.is_overload_stub:
            return False
        # Bodied @dispatch variants are self-contained (no separate impl);
        # emit a forward decl just like a regular function.
        if func.is_overload_stub and func.is_stub:
            return False

        self._gen_function_forward_decl_single(out, func)
        return True

    def _gen_function_forward_decl_single(
        self, out: TextIO, func: TpyFunction, *,
        cpp_name: str | None = None, return_type_override: TpyType | None = None,
    ) -> None:
        """Emit a single function forward declaration."""
        self.ctx.emit_declaration_echo(out, func.loc)
        name = escape_cpp_name(cpp_name or func.name)
        proto_params = self.protocols.get_all_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)
        rp = self._get_reassigned_params(func)
        mp = self._get_func_mutated_params(func)
        ae = self._get_func_addr_escapes(func)
        has_proto_params = bool(proto_params)
        has_fn_params = any(is_fn_type(pt) for _, pt in func.params)

        dfl = func.defaults if func.defaults else None
        effective_ret = return_type_override or func.return_type
        if is_generic or has_proto_params or has_fn_params:
            # Forward decl must carry the same Fn requires clause as the
            # definition; otherwise C++ treats them as two distinct overloads
            # and calls become ambiguous.
            out.write(self._gen_template_header_with_fn(func, proto_params))
            ret_type = self._resolve_return_type(effective_ret, const=func.is_readonly,
                                                  error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params, func.type_params,
                                                     mutated_params=mp,
                                                     defaults=dfl, emit_defaults=True,
                                                     func=func)
                      if has_proto_params or has_dynamic
                      else self.gen_params(func.params, func.type_params, reassigned_params=rp,
                                           mutated_params=mp, addr_escapes_params=ae,
                                           defaults=dfl, emit_defaults=True, func=func))
            out.write(f"{ret_type} {name}({params});\n")
        else:
            ret_type = self._resolve_return_type(effective_ret, const=func.is_readonly,
                                                  error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params,
                                                     mutated_params=mp,
                                                     defaults=dfl, emit_defaults=True,
                                                     func=func)
                      if has_dynamic
                      else self.gen_params(func.params, func.type_params, reassigned_params=rp,
                                           mutated_params=mp, addr_escapes_params=ae,
                                           defaults=dfl, emit_defaults=True, func=func))
            out.write(f"{ret_type} {name}({params});\n")

    def gen_function_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate a function declaration (or full definition for template functions).

        Non-template non-stub functions are skipped (already forward-declared).
        Returns True if something was emitted.
        """
        from ..parse.nodes import FunctionLinkage
        if func.linkage == FunctionLinkage.NATIVE:
            return False
        # @cpp_template functions expand inline at call sites -- no C++ declaration
        if func.cpp_template:
            return False
        # @builtin_function-keyed functions have sema/codegen handled
        # specially per call site; the stub itself has no C++ representation.
        if func.builtin_function_key is not None:
            return False

        # Bodyless @overload stubs are handled via the implementation function;
        # bodied @dispatch variants are self-contained functions.
        if func.is_overload_stub and func.is_stub:
            return False

        # @overload implementation with template params: emit specialized defs in header
        overload_stubs = self.ctx.analyzer.overload_groups.get(func)
        if overload_stubs:
            is_literal_only = self._overload_stubs_are_literal_only(overload_stubs, func)
            # Header-only when any stub (or the impl) is a template: a template
            # stub must be defined in the header to stay instantiable cross-module.
            if self.is_template_function(func):
                if is_literal_only:
                    for stub in overload_stubs:
                        self._gen_literal_specialized_function(out, func, stub)
                        out.write("\n")
                else:
                    for stub in overload_stubs:
                        self._gen_overload_specialized_function(out, func, stub, in_header=True)
                        out.write("\n")
                return True
            return False

        # @native(binding="C") and @export(binding="C") use extern "C" linkage
        if func.linkage in (FunctionLinkage.NATIVE_C, FunctionLinkage.EXPORT_C):
            c_name = func.native_name or func.name
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_c_params(func.params)
            out.write(f'extern "C" {ret_type} {c_name}({params});\n')
            return True

        proto_params = self.protocols.get_all_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)
        rp = self._get_reassigned_params(func)
        mp = self._get_func_mutated_params(func)
        has_proto_params = bool(proto_params)
        has_fn_params = any(is_fn_type(pt) for _, pt in func.params)

        if is_generic or has_proto_params or has_fn_params:
            # Template functions: emit full definition in header so that
            # importing modules can instantiate them.
            if func.is_stub:
                out.write(self._gen_template_header_with_fn(func, proto_params))
                ret_type = self._resolve_return_type(func.return_type)
                params = (self.gen_params_with_protocols(func.params, func.type_params,
                                                         mutated_params=mp)
                          if has_proto_params or has_dynamic
                          else self.gen_params(func.params, func.type_params,
                                               reassigned_params=rp, mutated_params=mp, func=func))
                out.write(f"{ret_type} {escape_cpp_name(func.name)}({params});\n")
            else:
                self.gen_function_def(out, func)
            return True

        # Non-template non-stub: already forward-declared
        if not func.is_stub:
            return False
        ret_type = self._resolve_return_type(func.return_type, const=func.is_readonly,
                                              error_return=func.error_return)
        params = (self.gen_params_with_protocols(func.params, mutated_params=mp)
                  if has_dynamic
                  else self.gen_params(func.params, func.type_params,
                                       reassigned_params=rp, mutated_params=mp, func=func))
        out.write(f"{ret_type} {escape_cpp_name(func.name)}({params});\n")
        return True

    def gen_extern_c_redecl(self, out: TextIO, func_info: FunctionInfo) -> None:
        """Emit an extern "C" re-declaration for a C-linkage function.

        This makes the C symbol visible in the current namespace without
        needing to trace through re-export chains or cross-module using
        declarations. Legal because extern "C" functions can be declared
        multiple times.
        """
        c_name = func_info.native_name or func_info.name
        ret_type = func_info.return_type.to_cpp_return()
        params = self.gen_c_params(func_info.params)
        out.write(f'extern "C" {ret_type} {c_name}({params});\n')

    def gen_function_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function definition."""
        from ..parse.nodes import FunctionLinkage
        # Bodyless @overload stubs are emitted via the implementation; bodied
        # @dispatch variants are self-contained and fall through.
        if func.is_overload_stub and func.is_stub:
            return
        # Stubs have no body -- declaration only
        if func.is_stub:
            return
        # @native (C++ import) exports are handled outside the namespace by generator.py
        if func.linkage == FunctionLinkage.NATIVE:
            return

        # @overload implementation: emit per-stub specialized functions
        overload_stubs = self.ctx.analyzer.overload_groups.get(func)
        if overload_stubs:
            # The shared implementation's source echoes once above the
            # group; each specialization below echoes only its stub line.
            self.ctx.emit_definition_echo(out, func.loc)
            if self._overload_stubs_are_literal_only(overload_stubs, func):
                for stub in overload_stubs:
                    self._gen_literal_specialized_function(out, func, stub)
                    out.write("\n")
                return
            else:
                for stub in overload_stubs:
                    self._gen_overload_specialized_function(out, func, stub)
                    out.write("\n")
                return

        self.ctx.emit_definition_echo(out, func.loc)

        if func.linkage == FunctionLinkage.EXPORT_C:
            c_name = func.native_name or func.name
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_c_params(func.params)
            out.write(f'extern "C" {ret_type} {c_name}({params}) {{\n')

            self.gen_body(out, func, return_cpp=ret_type)
            out.write("}\n")
            return

        proto_params = self.protocols.get_all_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)
        rp = self._get_reassigned_params(func)
        mp = self._get_func_mutated_params(func)
        ae = self._get_func_addr_escapes(func)
        has_proto_params = bool(proto_params)
        has_fn_params = any(is_fn_type(pt) for _, pt in func.params)

        if is_generic or has_proto_params or has_fn_params:
            # Generate combined template header for generic functions and/or protocol params
            # Skip default template args -- already emitted in the forward declaration
            out.write(self._gen_template_header_with_fn(
                func, proto_params, emit_defaults=False,
            ))
            ret_type = self._resolve_return_type(func.return_type, const=func.is_readonly,
                                                  error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params, func.type_params,
                                                     mutated_params=mp)
                      if has_proto_params or has_dynamic
                      else self.gen_params(func.params, func.type_params,
                                           reassigned_params=rp, mutated_params=mp,
                                           addr_escapes_params=ae, func=func))
            out.write(f"{ret_type} {escape_cpp_name(func.name)}({params}) {{\n")
        else:
            ret_type = self._resolve_return_type(func.return_type, const=func.is_readonly,
                                                  error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params, mutated_params=mp) if has_dynamic
                      else self.gen_params(func.params, func.type_params,
                                           reassigned_params=rp, mutated_params=mp,
                                           addr_escapes_params=ae, func=func))
            out.write(f"{ret_type} {escape_cpp_name(func.name)}({params}) {{\n")

        self.gen_body(out, func, return_cpp=ret_type)

        out.write("}\n")

    _overload_stubs_are_literal_only = staticmethod(overload_stubs_are_literal_only)

    _literal_mangled_name = staticmethod(literal_mangled_name)


    def _gen_literal_specialized_function(
        self, out: TextIO, impl: TpyFunction, stub: TpyFunction,
    ) -> None:
        """Generate a per-literal specialized C++ function.

        Uses the implementation's body with the stub's return type and
        injects literal narrowing facts for dead branch elimination.
        Name is mangled to avoid C++ signature collisions.
        """
        self.ctx.emit_declaration_echo(out, stub.loc)

        mangled = self._literal_mangled_name(impl.name, stub)
        rp = self._get_reassigned_params(impl)
        mp = self._get_func_mutated_params(impl)

        ret_type = self._resolve_return_type(stub.return_type, error_return=impl.error_return)
        params = self.gen_params(impl.params, impl.type_params,
                                 reassigned_params=rp, mutated_params=mp, func=impl)
        out.write(f"{ret_type} {escape_cpp_name(mangled)}({params}) {{\n")

        # Inject literal narrowing facts for dead branch elimination.
        # Uses literal_overload_facts which survives reset_scope (same
        # pattern as overload_param_types).
        for (pname, _), (_, stub_ptype) in zip(impl.params, stub.params):
            if isinstance(stub_ptype, LiteralType):
                self.ctx.literal_overload_facts[pname] = stub_ptype

        self.ctx.thir_overload_key = (impl, stub)
        try:
            self.gen_body(out, impl, return_cpp=ret_type)
        finally:
            self.ctx.literal_overload_facts = {}
            self.ctx.thir_overload_key = None

        out.write("}\n")

    def _gen_overload_specialized_function(
        self, out: TextIO, impl: TpyFunction, stub: TpyFunction, *,
        in_header: bool = False,
    ) -> None:
        """Generate a specialized C++ function for one @overload stub.

        Uses the implementation's body but with the stub's parameter types
        and return type. Sets overload_param_types so dead branch elimination
        kicks in for isinstance/match checks.

        When the stub is shorter than the impl, trailing impl params are
        filled in by the impl's defaults. Each missing param becomes a
        local variable at the top of the body and, when its default's type
        narrows the impl param, contributes a narrowing fact.

        Template-ness is derived from the STUB, not the impl, so it matches
        the forward declaration: a non-generic overload of a generic impl
        emits a non-template definition (else the called non-template symbol
        is never defined). Such a definition is header-only (the impl being a
        template routes the whole group to the header), so it is `inline` to
        stay ODR-safe -- hence the `in_header` flag.
        """
        self.ctx.emit_declaration_echo(out, stub.loc)

        impl_defaults = impl.defaults if impl.defaults else []
        missing_params = impl.params[len(stub.params):]

        overload_types = self._build_overload_narrowing(impl, stub, missing_params, impl_defaults)

        is_generic = bool(stub.type_params)
        rp = self._get_reassigned_params(impl)
        mp = self._get_func_mutated_params(impl)
        proto_params = self.protocols.get_all_protocol_params(stub.params)
        has_dynamic = self._has_dynamic_protocol_params(stub.params)
        has_proto_params = bool(proto_params)

        if is_generic or has_proto_params:
            out.write(self.protocols.gen_combined_template_header(
                stub.type_params, proto_params, stub.type_param_bounds,
                emit_defaults=False,
            ))
            ret_type = self._resolve_return_type(stub.return_type)
            params = (self.gen_params_with_protocols(stub.params, stub.type_params,
                                                     mutated_params=mp)
                      if has_proto_params or has_dynamic
                      else self.gen_params(stub.params, stub.type_params,
                                           reassigned_params=rp, mutated_params=mp, func=stub))
            out.write(f"{ret_type} {escape_cpp_name(stub.name)}({params}) {{\n")
        else:
            ret_type = self._resolve_return_type(stub.return_type)
            params = (self.gen_params_with_protocols(stub.params, mutated_params=mp) if has_dynamic
                      else self.gen_params(stub.params, stub.type_params,
                                           reassigned_params=rp, mutated_params=mp, func=stub))
            inline_kw = "inline " if in_header else ""
            out.write(f"{inline_kw}{ret_type} {escape_cpp_name(stub.name)}({params}) {{\n")

        # Request synthetic locals for missing impl params at the top of the body.
        self.ctx.overload_missing_param_locals = self._missing_param_local_specs(
            missing_params, impl_defaults, start_idx=len(stub.params))

        self.ctx.overload_param_types = overload_types
        # Promote literal narrowing to literal_overload_facts so that
        # equality-based dead-branch elim (if count == 0:) works alongside
        # isinstance-based elim (if x is None:).
        self._inject_literal_overload_facts(overload_types)
        self.ctx.thir_overload_key = (impl, stub)
        try:
            self.gen_body(out, impl, return_cpp=ret_type)
        finally:
            self.ctx.overload_param_types = {}
            self.ctx.overload_missing_param_locals = []
            self.ctx.literal_overload_facts = {}
            self.ctx.thir_overload_key = None

        out.write("}\n")

    def _inject_literal_overload_facts(self, narrowing: dict[str, TpyType]) -> None:
        """Promote IntLiteralType/LiteralType entries to literal_overload_facts.

        literal_overload_facts feeds into literal_facts at gen_body entry,
        enabling _resolve_literal_eq_statically to fold equality checks
        (e.g. ``if count == 0:``) when the narrowed param is a literal.
        """
        for pname, narrowed in narrowing.items():
            if isinstance(narrowed, LiteralType):
                self.ctx.literal_overload_facts[pname] = narrowed
            elif isinstance(narrowed, IntLiteralType) and narrowed.value is not None:
                self.ctx.literal_overload_facts[pname] = LiteralType(
                    BIGINT, (LiteralValue(LiteralTag.INT, narrowed.value),))

    def _build_overload_narrowing(
        self, impl: TpyFunction, stub: TpyFunction,
        missing_params: list[tuple[str, TpyType]],
        impl_defaults: list,
    ) -> dict[str, TpyType]:
        return build_overload_narrowing(impl, stub, missing_params,
                                        impl_defaults)

    @staticmethod
    def _missing_param_local_specs(
        missing_params: list[tuple[str, TpyType]],
        impl_defaults: list,
        *, start_idx: int,
    ) -> list[tuple[str, TpyType, TpyExpr]]:
        """Build (name, type, default_expr) specs for overload_missing_param_locals."""
        specs: list[tuple[str, TpyType, TpyExpr]] = []
        for offset, (pname, ptype) in enumerate(missing_params):
            default_expr = impl_defaults[start_idx + offset]
            specs.append((pname, ptype, default_expr))
        return specs

    def _gen_overload_specialized_method(
        self, out: TextIO, impl: TpyFunction, stub: TpyFunction,
        record_name: str, *,
        record_type_param_bounds: dict[str, TpyType] | None = None,
        dynamic_overrides: dict[str, bool] | None = None,
        mode: MethodEmitMode = "inline",
    ) -> None:
        """Generate a specialized C++ method for one @overload stub.

        Delegates to _gen_method_overload with stub's signature but impl's body,
        with the overload context set for dead branch elimination.
        """
        impl_defaults = impl.defaults if impl.defaults else []
        missing_params = impl.params[len(stub.params):]
        overload_types = self._build_overload_narrowing(
            impl, stub, missing_params, impl_defaults)

        # Create a synthetic TpyFunction with stub's types but impl's body.
        # Method const-ness (is_readonly) comes from the stub, since the stub's
        # decorators (@readonly, @auto_readonly) define the overload's const contract.
        # With parser cloning, @auto_readonly stubs are already split into separate
        # mutable/const clones before codegen runs.
        synth = TpyFunction(
            name=stub.name,
            params=list(stub.params),
            return_type=stub.return_type,
            body=impl.body,
            is_method=impl.is_method,
            is_staticmethod=impl.is_staticmethod,
            is_classmethod=impl.is_classmethod,
            is_readonly=stub.is_readonly,
            readonly_opt_out=stub.readonly_opt_out,
            is_pure=impl.is_pure,
            is_consuming=impl.is_consuming,
            type_params=list(impl.type_params),
            type_param_bounds=dict(impl.type_param_bounds),
            defaults=list(stub.defaults) if stub.defaults else [],
            loc=stub.loc,
        )
        # Copy scan results from impl so codegen can find pre-scan data
        self.ctx.analyzer.function_scan_results[synth] = (
            self.ctx.analyzer.function_scan_results.get(impl, None)
        )
        if impl in self.ctx.analyzer.function_hoisted_vars:
            self.ctx.analyzer.function_hoisted_vars[synth] = (
                self.ctx.analyzer.function_hoisted_vars[impl]
            )
        if impl in self.ctx.analyzer.function_frame_local_roots:
            self.ctx.analyzer.function_frame_local_roots[synth] = (
                self.ctx.analyzer.function_frame_local_roots[impl]
            )
        if impl in self.ctx.analyzer.function_closed_frames:
            self.ctx.analyzer.function_closed_frames[synth] = (
                self.ctx.analyzer.function_closed_frames[impl]
            )
        if impl in self.ctx.analyzer.function_move_through_vars:
            self.ctx.analyzer.function_move_through_vars[synth] = (
                self.ctx.analyzer.function_move_through_vars[impl]
            )
        if impl in self.ctx.analyzer.function_movable_locals:
            self.ctx.analyzer.function_movable_locals[synth] = (
                self.ctx.analyzer.function_movable_locals[impl]
            )

        # Set overload context
        self.ctx.overload_param_types = overload_types
        self._inject_literal_overload_facts(overload_types)
        self.ctx.overload_missing_param_locals = self._missing_param_local_specs(
            missing_params, impl_defaults, start_idx=len(stub.params))
        self.ctx.thir_overload_key = (impl, stub)
        try:
            self.gen_method_def(out, synth, record_name, dynamic_overrides,
                                record_type_param_bounds=record_type_param_bounds,
                                mode=mode)
        finally:
            self.ctx.overload_param_types = {}
            self.ctx.overload_missing_param_locals = []
            self.ctx.literal_overload_facts = {}
            self.ctx.thir_overload_key = None

    def _gen_literal_specialized_method(
        self, out: TextIO, impl: TpyFunction, stub: TpyFunction,
        record_name: str, *,
        record_type_param_bounds: dict[str, TpyType] | None = None,
        dynamic_overrides: dict[str, bool] | None = None,
        mode: MethodEmitMode = "inline",
    ) -> None:
        """Generate a per-literal specialized C++ method.

        Uses the implementation's body with the stub's return type and
        a mangled method name. Injects literal narrowing facts for dead
        branch elimination.
        """
        mangled = literal_mangled_name(impl.name, stub)
        synth = TpyFunction(
            name=mangled,
            params=list(impl.params),
            return_type=stub.return_type,
            body=impl.body,
            is_method=impl.is_method,
            is_staticmethod=impl.is_staticmethod,
            is_classmethod=impl.is_classmethod,
            is_readonly=impl.is_readonly,
            readonly_opt_out=impl.readonly_opt_out,
            is_pure=impl.is_pure,
            is_consuming=impl.is_consuming,
            type_params=list(impl.type_params),
            type_param_bounds=dict(impl.type_param_bounds),
            defaults=list(impl.defaults) if impl.defaults else [],
            loc=stub.loc,
        )
        # Copy scan results from impl
        self.ctx.analyzer.function_scan_results[synth] = (
            self.ctx.analyzer.function_scan_results.get(impl, None)
        )
        if impl in self.ctx.analyzer.function_hoisted_vars:
            self.ctx.analyzer.function_hoisted_vars[synth] = (
                self.ctx.analyzer.function_hoisted_vars[impl]
            )
        if impl in self.ctx.analyzer.function_frame_local_roots:
            self.ctx.analyzer.function_frame_local_roots[synth] = (
                self.ctx.analyzer.function_frame_local_roots[impl]
            )
        if impl in self.ctx.analyzer.function_closed_frames:
            self.ctx.analyzer.function_closed_frames[synth] = (
                self.ctx.analyzer.function_closed_frames[impl]
            )
        if impl in self.ctx.analyzer.function_move_through_vars:
            self.ctx.analyzer.function_move_through_vars[synth] = (
                self.ctx.analyzer.function_move_through_vars[impl]
            )
        if impl in self.ctx.analyzer.function_movable_locals:
            self.ctx.analyzer.function_movable_locals[synth] = (
                self.ctx.analyzer.function_movable_locals[impl]
            )

        # Inject literal narrowing facts
        for (pname, _), (_, stub_ptype) in zip(impl.params, stub.params):
            if isinstance(stub_ptype, LiteralType):
                self.ctx.literal_overload_facts[pname] = stub_ptype

        self.ctx.thir_overload_key = (impl, stub)
        try:
            self.gen_method_def(out, synth, record_name, dynamic_overrides,
                                record_type_param_bounds=record_type_param_bounds,
                                mode=mode)
        finally:
            self.ctx.literal_overload_facts = {}
            self.ctx.thir_overload_key = None

    def _get_dynamic_override_info(self, record_name: str) -> dict[str, bool]:
        """Get map of method_name -> is_const for methods overriding @dynamic protocol virtuals.

        Walks the MRO so that a record inheriting a @dynamic protocol transitively
        (via a concrete-class parent) still emits `override` on its method
        redefinitions, not non-virtual hiding methods. Direct + transitive both
        contribute -- the C++ base class emits pure virtuals for the inherited
        protocol slot in either case.
        """
        record_info = self.ctx.analyzer.registry.get_record(record_name)
        if not record_info:
            return {}
        result: dict[str, bool] = {}
        for proto, proto_info in self.ctx.analyzer.registry.iter_dynamic_protocols(record_info):
            for method_sig in self.protocols.collect_concept_methods(proto.name):
                # First-wins (MRO is nearest-first; most-derived protocol
                # decides the slot's const-ness). Diamond hierarchies with
                # conflicting const-ness across sibling protocols for the
                # same method name are latent -- the first visited may not
                # match a sibling's declaration and would silently emit an
                # override that doesn't satisfy the sibling's virtual slot.
                # See TODO.md (multi-protocol diamond const-merge).
                if method_sig.name not in result:
                    result[method_sig.name] = method_sig.is_readonly or proto_info.is_readonly
        return result

    def gen_shim_params(self, method: TpyFunction, record_name: str) -> str:
        """Param list for a delegating operator shim (operator(), the mutable
        operator[] overload), rendered with the same const decisions as the
        target method's own signature.

        A shim that re-derives const-ness from declared types drifts from the
        method emit whenever inference diverges (a mutated or borrow-escaping
        param stays mutable in the method): the shim then binds const and the
        delegation doesn't type-check. Mirrors gen_method_def's non-protocol
        arm; defaults and the reassigned-param rename are omitted (the shim
        declares its own params and forwards by name, so the rename never
        applies, and shims don't repeat C++ default arguments).
        """
        use_const_params = ((method.is_readonly and not method.auto_readonly_params_resolved)
                            or method.name in CONST_PARAMS_METHODS)
        ae = self._get_method_addr_escapes(method, record_name)
        if use_const_params:
            return self.gen_params(method.params, method.type_params, const_params=True,
                                   mutated_params=self._get_method_genuine_mutated_params(method, record_name),
                                   addr_escapes_params=ae,
                                   func=method)
        return self.gen_params(method.params, method.type_params,
                               mutated_params=self._get_method_mutated_params(method, record_name),
                               addr_escapes_params=ae,
                               func=method)

    def gen_method_def(self, out: TextIO, method: TpyFunction, record_name: str,
                       dynamic_overrides: dict[str, bool] | None = None,
                       record_type_param_bounds: dict[str, TpyType] | None = None,
                       mode: MethodEmitMode = "inline") -> None:
        """Generate a method definition for a record. ``mode`` is
        forwarded to ``_gen_method_overload``.
        """
        # escape_cpp_name handles names that collide with C++ keywords
        # (e.g. `def double` -> `double_`); call sites apply the same escape.
        cpp_name = escape_cpp_name(method.name)
        cpp_return_type = method.return_type

        # Property setter: rename to set_<name> in C++
        if method.is_property_setter:
            cpp_name = f"set_{escape_cpp_name(method.property_name or method.name)}"

        is_const = method.is_readonly
        is_static = method.is_staticmethod

        # Determine if this method overrides a @dynamic protocol virtual
        override_const: bool | None = None
        if dynamic_overrides and method.name in dynamic_overrides:
            override_const = dynamic_overrides[method.name]

        if is_const and not is_static:
            # @readonly: single const overload. C++ allows calling const methods
            # on non-const objects, so no non-const duplicate needed.
            is_override = override_const is True
            self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type,
                                      const=True, override=is_override,
                                      record_type_param_bounds=record_type_param_bounds,
                                      mode=mode)
        else:
            is_override = override_const is False and not is_static  # base is non-const
            self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type, const=False,
                                      static=is_static, override=is_override,
                                      record_type_param_bounds=record_type_param_bounds,
                                      mode=mode)

    def _gen_method_overload(
        self, out: TextIO, method: TpyFunction, record_name: str,
        cpp_name: str, cpp_return_type: TpyType, *, const: bool, static: bool = False,
        override: bool = False, record_type_param_bounds: dict[str, TpyType] | None = None,
        mode: MethodEmitMode = "inline",
    ) -> None:
        """Emit a single method overload. See ``MethodEmitMode`` for the
        contract of ``mode``.
        """
        # ``def_*`` modes emit at namespace scope -- the class scope only
        # opens after the qualified method name, so the return type and
        # method-name parts must use the fully-qualified form
        # (``Outer::Inner``). In-class emission uses the short name.
        is_def_mode = mode in ("def_hpp", "def_cpp")
        cpp_record_qualified = escape_cpp_name(record_name.replace(".", "::"))
        rec_short = bare_name(record_name)
        # The receiver's const-ness and the return's are separate verdicts: an
        # inferred-const method whose return borrows a parameter stays `const`
        # and keeps its declared mutable return.
        ret_const = const and self._method_return_const_projected(method, record_name)

        # Inplace dunders return T& (reference to self) in C++.
        is_inplace_dunder = method.name in CONST_PARAMS_METHODS
        if is_inplace_dunder:
            ret_type_class = cpp_record_qualified if is_def_mode else escape_cpp_name(rec_short)
            ret_type = f"{ret_type_class}&"
        elif method.is_property_getter:
            # A property getter returns a reference to the field. Which shapes
            # take the STORAGE spelling rather than the method convention is
            # `property_getter_returns_storage_ref`'s call -- the same one the
            # value-category rule reads to classify a call of this getter.
            inner = unwrap_ref_type(cpp_return_type)
            if property_getter_returns_storage_ref(inner):
                # The union storage type is READ rather than spelled from a
                # head: a reference to the BASE variant binds to the field and
                # then compares index-first, silently losing the storage
                # form's comparison rule at every use of the property.
                storage = (f"std::optional<{inner.inner.to_cpp()}>"
                           if isinstance(inner, OptionalType)
                           else self.types.type_to_cpp(inner))
                ret_type = f"const {storage}&" if const else f"{storage}&"
            else:
                ret_type = self._resolve_return_type(cpp_return_type, const=ret_const,
                                                      error_return=method.error_return)
        elif override and is_any_str_type(cpp_return_type):
            # @dynamic protocol virtual returns std::string; override must match
            # even if the impl declares -> StrView or -> String.
            ret_type = "std::string"
        else:
            ret_type = self._resolve_return_type(cpp_return_type, const=ret_const,
                                                  error_return=method.error_return)
        dfl = method.defaults if method.defaults else None

        proto_params = self.protocols.get_all_protocol_params(method.params)
        has_dynamic = self._has_dynamic_protocol_params(method.params)
        use_protocol_params = bool(proto_params) or has_dynamic

        # Determine method-level type params (not in the class template)
        record_info = self.ctx.analyzer.registry.get_record(record_name)
        class_type_params = set(record_info.type_params) if record_info and record_info.type_params else set()
        new_method_params = [tp for tp in (method.type_params or []) if tp not in class_type_params]
        class_param_bounds = {tp: method.type_param_bounds[tp]
                              for tp in (method.type_params or [])
                              if tp in class_type_params and tp in method.type_param_bounds}

        # Inplace dunders (__iadd__ etc.) mutate self but take const params.
        # For auto_readonly_params_resolved, the params already carry ReadonlyType
        # from the parser clone -- don't blanket-apply const.
        use_const_params = ((const and not method.auto_readonly_params_resolved)
                            or method.name in CONST_PARAMS_METHODS)
        rp = self._get_reassigned_params(method)
        mp = self._get_method_mutated_params(method, record_name)
        ae = self._get_method_addr_escapes(method, record_name)
        # For const methods, drop the params marked mutated only because they're
        # returned by reference (return-borrow), keeping genuinely mutated ones.
        gmp = self._get_method_genuine_mutated_params(method, record_name) if use_const_params else None
        # C++ rejects default arguments repeated on both the in-class declaration
        # and the out-of-line definition. Emit defaults only on the decl side.
        emit_defaults = not is_def_mode
        # A @dynamic-virtual override must match the base's param TYPES (C++
        # matches overrides by type, const included). The base renders params
        # at their declared const-ness (no body => no const-inference), so the
        # override suppresses inference too (mutated_params=None => const only
        # for an explicit ReadonlyType) or it emits `const T*` against the base
        # `T*` and the class stays abstract. The body const-sets and
        # FunctionInfo.const_borrow_params deliberately keep the real mutated
        # facts -- stricter than this signature, which is safe (a const local
        # binds from a non-const param) and unreachable for the verdict until
        # @dynamic overrides become THIR-eligible.
        declared_const_only = override
        if use_protocol_params:
            if use_const_params and not declared_const_only:
                params = self.gen_params_with_protocols(method.params, method.type_params,
                                                        const_params=True,
                                                        mutated_params=gmp,
                                                        defaults=dfl, emit_defaults=emit_defaults,
                                                        func=method)
            else:
                params = self.gen_params_with_protocols(method.params, method.type_params,
                                                        mutated_params=None if declared_const_only else mp,
                                                        defaults=dfl, emit_defaults=emit_defaults,
                                                        func=method)
        else:
            ctp = class_type_params or None
            if use_const_params and not declared_const_only:
                params = self.gen_params(method.params, method.type_params, const_params=True,
                                         mutated_params=gmp,
                                         addr_escapes_params=ae,
                                         defaults=dfl, emit_defaults=emit_defaults,
                                         class_type_params=ctp, func=method)
            else:
                use_ro = const and not method.auto_readonly_params_resolved and not declared_const_only
                params = self.gen_params(method.params, method.type_params,
                                         reassigned_params=rp,
                                         mutated_params=None if declared_const_only else mp,
                                         addr_escapes_params=ae,
                                         defaults=dfl, emit_defaults=emit_defaults,
                                         class_type_params=ctp, func=method)
        const_suffix = " const" if const else ""
        # auto_own borrowing clone needs & qualifier so C++ can distinguish
        # f() & from f() && (both must have ref-qualifiers or neither).
        lvalue_suffix = " &" if method.is_auto_own_borrowing_clone else ""
        rvalue_suffix = " &&" if method.is_consuming else ""
        override_suffix = " override" if override else ""
        static_prefix = "static " if static else ""

        # Indent / qualifier / line-prefix differ by mode:
        # - inline / decl: lives inside the struct, indented.
        # - def_hpp / def_cpp: lives at namespace scope, no leading indent,
        #   class-qualified method name, no static/override qualifiers. The
        #   two ``def_*`` variants differ only in whether ``inline`` is
        #   needed for ODR (yes in the header, no in the source TU).
        sig_indent = "" if is_def_mode else INDENT
        body_indent_level = 1 if is_def_mode else 2
        body_indent = INDENT * body_indent_level

        # Build requires clause for per-method bounds on class type params
        # (only fires for templated records, which are blocked from out-of-line
        # by `_method_can_be_out_of_line` -- so `sig_indent` is always INDENT
        # here, but keep the binding so future loosening doesn't misalign).
        requires_clause = ""
        if class_param_bounds:
            req_parts = []
            for tp, bound in class_param_bounds.items():
                concept_name = self.protocols.get_concept_name(bound)
                if bound.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in bound.type_args)
                    req_parts.append(f"{concept_name}<{type_args_cpp}, {tp}>")
                else:
                    req_parts.append(f"{concept_name}<{tp}>")
            requires_clause = f"\n{sig_indent}  requires {' && '.join(req_parts)}"
        if is_def_mode:
            inline_prefix = "inline " if mode == "def_hpp" else ""
            qualified_name = f"{cpp_record_qualified}::{cpp_name}"
            sig_static_prefix = ""
            sig_override_suffix = ""
        else:
            inline_prefix = ""
            qualified_name = cpp_name
            sig_static_prefix = static_prefix
            sig_override_suffix = override_suffix

        out.write("\n")
        if mode == "decl":
            self.ctx.emit_declaration_echo(out, method.loc, sig_indent)
        else:
            self.ctx.emit_definition_echo(out, method.loc, sig_indent)
        fn_params = self._collect_fn_params(method.params)
        if proto_params or new_method_params or fn_params:
            # Bounds for new method type params only (class param bounds go on the requires clause)
            bounds_for_header = dict(record_type_param_bounds) if record_type_param_bounds else {}
            bounds_for_header.update(
                {k: v for k, v in method.type_param_bounds.items()
                 if k in set(new_method_params)}
            )
            base_header = self.protocols.gen_combined_template_header(
                new_method_params, proto_params, bounds_for_header,
            )
            if fn_params:
                fn_tpl_parts, fn_req_parts = self._gen_fn_template_parts(fn_params)
                template_header = self._merge_fn_into_header(
                    base_header, fn_tpl_parts, fn_req_parts, indent=sig_indent)
            else:
                template_header = base_header
            out.write(f"{sig_indent}{template_header}")
        ref_suffix = rvalue_suffix or lvalue_suffix
        sig_line = (f"{sig_indent}{inline_prefix}{sig_static_prefix}{ret_type} "
                    f"{qualified_name}({params}){const_suffix}{ref_suffix}"
                    f"{sig_override_suffix}{requires_clause}")
        if mode == "decl":
            out.write(f"{sig_line};\n")
            return
        out.write(f"{sig_line} {{\n")

        # Consuming methods on types with __del__: suppress destructor at method entry.
        # Walk the parent chain since __del__ may be inherited.
        if method.is_consuming and record_info is not None and self.ctx.record_or_ancestor_has_del(record_name):
            out.write(f"{body_indent}this->__tpy_owned_ = false;\n")

        prev_consuming = self.ctx.in_consuming_method
        self.ctx.in_consuming_method = method.is_consuming
        self.gen_body(out, method,
                      indent_level=body_indent_level, return_cpp=ret_type)
        self.ctx.in_consuming_method = prev_consuming

        out.write(f"{sig_indent}}}\n")

    def gen_body(self, out: TextIO, func: TpyFunction,
                 indent_level: int = 1, return_cpp: str | None = None) -> None:
        """Emit one function/method body from its lowered THIR."""
        # Local import: `thir.emit` reaches back into `codegen_cpp`, so an
        # eager import here would close a codegen_cpp <-> thir cycle.
        from ..thir.emit import ModuleCounter, TempSink, emit_thir_body
        overload_key = self.ctx.thir_overload_key
        if overload_key is not None:
            # Per-stub specialization in flight: only the (impl, stub) entry
            # may emit this body -- falling back to the plain map would hijack
            # the specialization with the unspecialized lowering. Consume the
            # key so nested bodies never see it.
            self.ctx.thir_overload_key = None
            impl, stub = overload_key
            per_stub = self.ctx.thir_overload_functions.get(impl)
            thir_fn = None if per_stub is None else per_stub.get(stub)
        else:
            thir_fn = self.ctx.thir_functions.get(func)
        if thir_fn is None:
            raise CodeGenError(
                f"internal error: no lowered body for '{func.name}'", func.loc)
        with self.ctx.temps.function_scope():
            emit_thir_body(out, thir_fn, indent_level,
                           temps=TempSink(self.ctx),
                           with_counter=ModuleCounter(self.ctx, "with_counter"),
                           try_counter=ModuleCounter(self.ctx, "try_except_counter"),
                           finally_guard_counter=ModuleCounter(
                               self.ctx, "finally_guard_counter"),
                           return_cpp=return_cpp)

    def seed_param_locals(self, *args, **kwargs) -> None:
        """Delegate to the shared emit primitive."""
        emit_prims.seed_param_locals(self.ctx, self.protocols, *args, **kwargs)

    def seed_param_locals_scoped(self, *args, **kwargs):
        """Delegate to the shared emit primitive."""
        return emit_prims.seed_param_locals_scoped(
            self.ctx, self.protocols, *args, **kwargs)

    def _resolve_global_type(self, stmt: TpyVarDecl) -> TpyType:
        """Resolve the type of a global variable, unwrapping Own[T]/Optional[T] to T."""
        if stmt.init:
            var_type = resolve_global_binding_type(
                stmt, self.ctx.analyzer, self.types)
        elif stmt.type:
            var_type = stmt.type
        else:
            raise RuntimeError(f"Global '{stmt.name}' has no type and no initializer")
        if isinstance(var_type, OwnType):
            var_type = var_type.wrapped
        # Optional non-value types use inner type (pointer-global adds T*)
        elif isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
            var_type = var_type.inner
        # Resolve IntLiteralType in all composite types (tuples, arrays, lists)
        var_type = resolve_int_literals(var_type, self.ctx.analyzer.ctx.default_int_for_literal)
        return var_type

    def _global_cpp_type(self, var_type: TpyType, stmt: TpyVarDecl | None = None) -> str:
        """Map a global variable type to C++.

        @dynamic protocol types use the base class name instead of the concept
        template placeholder, since globals need a concrete pointer type.
        Structural protocol types use decltype(init_expr) since they have no
        concrete C++ type name (they map to C++ concepts).
        """
        if is_protocol_type(var_type) and isinstance(var_type, NominalType):
            pi = protocol_info_of(var_type)
            if pi and pi.is_dynamic:
                return self.protocols.get_dynamic_base_name(var_type)
            # Structural protocol: use decltype(init_expr) to let C++ deduce
            if stmt and stmt.init:
                dt = self._build_decltype_expr(stmt.init)
                if dt is not None:
                    return f"decltype({dt})"
            raise CodeGenError(
                f"Cannot determine C++ type for global '{stmt.name if stmt else '?'}' "
                f"with structural protocol type '{var_type}'",
                loc=stmt.loc if stmt else None,
            )
        return var_type.to_cpp()

    def _build_decltype_expr(self, init: TpyExpr) -> str | None:
        """Build a C++ expression for use inside decltype() from an init AST.

        Handles simple function/builtin calls with name arguments.  Returns
        None when the expression is too complex.
        """
        if not isinstance(init, TpyCall):
            return None
        fi = init.resolved_function_info
        if fi is None:
            return None
        template = fi.cpp_template
        if template is None and fi.native_name and not fi.native_function:
            template = f"::{fi.native_name}({', '.join(f'{{{i}}}' for i in range(len(init.args)))})"
        if template is None:
            return None
        arg_strs: list[str] = []
        for arg in init.args:
            if isinstance(arg, TpyName):
                if arg.name in self.ctx.pointer_globals:
                    arg_strs.append(f"(*{arg.name})")
                else:
                    arg_strs.append(arg.name)
            else:
                return None
        try:
            return expand_cpp_template(template, None, *arg_strs)
        except CodeGenError:
            return None

    def gen_global_decl(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a global variable definition in source file.

        Value-type globals are plain T, non-value-type globals are T* (nullptr).
        Initialization happens in __tpy_init() to ensure proper execution order.
        """
        if stmt.linkage != VarLinkage.DEFAULT:
            return
        var_type = self._resolve_global_type(stmt)
        cpp_type = self._global_cpp_type(var_type, stmt)
        # Global-slot storage is a value-vs-pointer-storage question: a
        # force_pointer_repr Optional is T* at a boundary but a value-stored
        # std::optional<T> in a global, so this asks the storage predicate, not
        # a boundary-form one.
        is_value = var_type.is_value_type() or var_type.needs_wrapper()
        if is_borrow_form_tuple_global(var_type):
            # A tuple of pointer slots, null until module init binds it.
            cpp_type = self.types.tuple_borrow_cpp(var_type)
            init = emit_prims.placeholder_init(var_type, cpp_type) or "{}"
            out.write(f"{cpp_type} {stmt.name}{init};\n")
        elif is_value:
            # C++ primitives need explicit zero-init; class types (BigInt, string_view) don't
            init = (emit_prims.placeholder_init(var_type, cpp_type)
                    or ("{}" if (is_primitive_type(var_type)
                                 or isinstance(var_type, PtrType)) else ""))
            out.write(f"{cpp_type} {stmt.name}{init};\n")
        else:
            out.write(f"{cpp_type}* {stmt.name}{{}};\n")

    def gen_global_extern(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate an extern declaration for a global variable in header file."""
        var_type = self._resolve_global_type(stmt)
        cpp_type = self._global_cpp_type(var_type, stmt)
        is_value = var_type.is_value_type() or var_type.needs_wrapper()
        if is_borrow_form_tuple_global(var_type):
            out.write(f"extern {self.types.tuple_borrow_cpp(var_type)} "
                      f"{stmt.name};\n")
        elif is_value:
            out.write(f"extern {cpp_type} {stmt.name};\n")
        else:
            out.write(f"extern {cpp_type}* {stmt.name};\n")

    def _gen_final_init_expr(self, stmt: TpyVarDecl, var_type: TpyType) -> str:
        """Render a Final global's initializer through THIR. The name itself
        is excluded from its own scope: sema rejects a self- or
        forward-reference, so admitting it would seed a binding no initializer
        can legally read."""
        # `thir.constants` imports `thir.emit`, so an eager import here would
        # be a codegen_cpp <-> thir cycle; `thir.reject` only rides along.
        from ..thir.constants import final_global_scope, lower_constant
        from ..thir.reject import begin_attempt, commit_attempt, reject_attempt
        scope = final_global_scope(
            self.ctx.analyzer,
            (n for n in self.ctx.final_globals if n != stmt.name))
        begin_attempt()
        init = lower_constant(
            stmt.init, var_type, self.ctx.analyzer, const_scope=scope,
            render_type=self.types.type_to_cpp,
            render_type_stored=self.types.type_to_cpp_stored,
            render_resolve=self.types.resolve_type)
        if init is None:
            reject_attempt("final_global",
                           where=f"in the initializer of '{stmt.name}'",
                           loc=stmt.loc)
        commit_attempt()
        return init

    def gen_final_global_header(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a Final global declaration in header file.

        Constexpr-eligible types: inline constexpr T NAME = VALUE;
        BigInt: extern const ::tpy::BigInt NAME;

        Special case: Final[str] uses std::string_view (string literals have
        static lifetime, constexpr requires literal type).
        """
        self.ctx.emit_declaration_echo(out, stmt.loc)
        var_type = self._resolve_global_type(stmt)
        cpp_type = var_type.to_cpp()
        # Final[str] -> constexpr std::string_view (string literals are static)
        if is_str_type(var_type):
            cpp_type = "std::string_view"
        if is_constexpr_eligible(var_type):
            init_expr = self._gen_final_init_expr(stmt, var_type)
            out.write(f"inline constexpr {cpp_type} {stmt.name} = {init_expr};\n")
        else:
            # BigInt and other non-constexpr types: extern const in header
            out.write(f"extern const {cpp_type} {stmt.name};\n")

    def gen_final_global_source(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a Final global definition in source file.

        Only needed for non-constexpr types (BigInt). Constexpr types are
        fully defined in the header via inline constexpr.
        """
        var_type = self._resolve_global_type(stmt)
        if is_constexpr_eligible(var_type):
            return  # Defined in header via inline constexpr
        self.ctx.emit_declaration_echo(out, stmt.loc)
        cpp_type = var_type.to_cpp()
        init_expr = self._gen_final_init_expr(stmt, var_type)
        out.write(f"const {cpp_type} {stmt.name} = {init_expr};\n")

    def gen_module_init_decl(self, out: TextIO) -> None:
        """Generate module init function declaration in header."""
        out.write("void __tpy_init();\n")

    def gen_module_init(self, out: TextIO, stmts: list, global_types: dict[str, TpyType | None] | None = None,
                        has_user_main: bool = False, module_name: str = "__main__") -> None:
        """Generate module init function containing top-level statements.

        Args:
            out: Output stream.
            stmts: Top-level statements (including TpyImport for user module imports).
            global_types: Dict of global variable names to types.
            has_user_main: If True, call main() at end.
            module_name: Value for __name__ ("__main__" for entry point, module name otherwise).
        """
        self.ctx.emit_module_source_block(out, stmts)
        out.write("void __tpy_init() {\n")
        # Guard against double initialization (handles diamond dependencies)
        out.write(f"{INDENT}static bool initialized = false;\n")
        out.write(f"{INDENT}if (initialized) return;\n")
        out.write(f"{INDENT}initialized = true;\n\n")

        self.ctx.reset_scope()
        # Pre-seed with global names and types so re-declarations become assignments
        if global_types:
            self.ctx.declared_vars = set(global_types.keys())
            self.ctx.var_types = {name: typ for name, typ in global_types.items() if typ is not None}
            self.ctx.pointer_locals = {
                name for name, typ in global_types.items()
                if typ and not typ.is_value_type() and not typ.needs_wrapper()
            }
        self.ctx.slots.reset(global_scope=True)
        scan = self.ctx.analyzer.top_level_scan_result
        if scan:
            self.ctx.reassigned_vars = scan.reassigned - self.ctx.global_declared_vars
            self.ctx.rvalue_reassigned_vars = scan.rvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.lvalue_reassigned_vars = scan.lvalue_reassigned - self.ctx.global_declared_vars
        else:
            self.ctx.reassigned_vars = set()
            self.ctx.rvalue_reassigned_vars = set()
            self.ctx.lvalue_reassigned_vars = set()
        self.ctx.hoisted_vars = self.ctx.analyzer.top_level_hoisted_vars.copy()
        self.ctx.move_through_vars = self.ctx.analyzer.top_level_move_through_vars.copy()
        self.ctx.current_ns = self.ctx.analyzer.global_ns
        self.ctx.indent_level = 1

        from ..thir.emit import ModuleCounter, TempSink, emit_thir_body
        # `global_scope`: slots spell `static __global_slot_N` at this scope.
        # The ctx seeding above still runs -- gen_main and the record/global
        # emitters read it after this call.
        with self.ctx.temps.function_scope():
            emit_thir_body(out, self.ctx.thir_top_level, 1,
                           temps=TempSink(self.ctx),
                           with_counter=ModuleCounter(self.ctx, "with_counter"),
                           try_counter=ModuleCounter(self.ctx, "try_except_counter"),
                           finally_guard_counter=ModuleCounter(
                               self.ctx, "finally_guard_counter"),
                           global_scope=True)

        self.ctx.current_ns = None
        if has_user_main:
            out.write(f"{INDENT}main();\n")
        out.write("}\n\n")

    def gen_namespace_close(self, out: TextIO) -> None:
        """Close the namespace in source file (for non-entry-point modules)."""
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n")

    def gen_main(self, out: TextIO, no_main: bool = False) -> None:
        """Generate __tpy_main() and optionally C++ main().

        Always emits __tpy_main(argc, argv) at global scope which initializes
        sys_argv and calls the entry module's __tpy_init().
        When no_main is False (default), also emits a main() wrapper.
        """
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n\n")
        out.write("int __tpy_main(int argc, char* argv[]) {\n")
        out.write(f"{INDENT}::tpy::init_sys_argv(argc, argv);\n")
        out.write(f"{INDENT}{qualified_cpp_name(self.ctx.module_name, '__tpy_init')}();\n")
        out.write(f"{INDENT}return 0;\n")
        out.write("}\n")
        if not no_main:
            out.write("\nint main(int argc, char* argv[]) {\n")
            out.write(f"{INDENT}::tpy::process_startup();\n")
            out.write(f"{INDENT}return __tpy_main(argc, argv);\n")
            out.write("}\n")

    @staticmethod
    def _split_native_name(name: str) -> tuple[str, str]:
        """Split a qualified C++ name into (namespace, bare_name).

        'physics::calc' -> ('physics', 'calc')
        'a::b::func'    -> ('a::b', 'func')
        'func'           -> ('', 'func')
        """
        idx = name.rfind("::")
        if idx == -1:
            return ("", name)
        return (name[:idx], name[idx + 2:])

