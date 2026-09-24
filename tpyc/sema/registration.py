"""
TurboPython Type Registration

Registers builtin types, records, protocols, and functions.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace, fields as _dc_fields
from typing import TYPE_CHECKING


def _adopt_skeleton(skeleton, full):
    """Copy every dataclass field of `full` onto `skeleton` in place
    and return the skeleton. Peers' analyzer registries capture a
    reference to the skeleton during `bind_imports`; mutating its
    fields in place propagates freshly-finalized data without
    orphaning peer references. The name-equality check guards
    against a future `register_*` site picking up the wrong
    skeleton.
    """
    assert skeleton.name == full.name, (
        f"skeleton/full name mismatch: {skeleton.name!r} vs {full.name!r}"
    )
    for fld in _dc_fields(skeleton.__class__):
        setattr(skeleton, fld.name, getattr(full, fld.name))
    return skeleton

from ..typesys import (
    TpyType, NominalType, TypeParamRef, SelfType, RecordInfo, FieldInfo, FunctionInfo, FunctionLinkage, PropertyInfo, is_fn_type, contains_fn_type,
    TypeParamKind, OwnType, VoidType, ParamInfo, MethodSignature, ProtocolInfo, is_protocol_type, AnyType, PtrType, RefType,
    ReadonlyType, InteriorMutableType,
    is_c_abi_allowed, c_abi_type_hint, C_ABI_TYPE_ERROR,
    type_contains_own,
    IMPLICIT_READONLY_METHODS, CONST_PARAMS_METHODS, FinalType, make_span, make_varargs,
    is_final_allowed_inner, FINAL_INNER_TYPE_ERROR, try_unwrap_class_constant,
    is_void_like_type,
    is_classvar_allowed_inner, CLASSVAR_INNER_TYPE_ERROR,
    STRVIEW, INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64, BIGINT, BOOL, NONE, TupleType, final_type_str_to_strview,
    make_awaitable,
    make_cancellable,
    register_return_exception, is_return_exception,
    attach_type_param_bounds,
    has_auto_readonly, has_auto_own,
    qualify_exception_name, ensure_qualified,
    OptionalType,
    C3LinearizationError,
    same_base_type,
    bare_name,
    contains_type_param,
    contains_type_kind_param,
    unwrap_readonly,
    unwrap_ref_type,
)
from ..module_names import public_module_name
from ..parse import (
    TpyRecord, TpyProtocol, TpyEnum, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, RecordLinkage,
    TpyAssign, TpyFieldAccess, TpyName, TpyBinOp, TpyReturn, TpyMethodCall, TpyCall, TpyExprStmt,
    TpyNoneLiteral, TpyStrLiteral, TpyRaise, TpyTry, collect_name_refs,
)
from ..parse.nodes import returns_borrow_rooted_at_self
from ..parse.parser import auto_declare_fields_from_init, reorder_fields_by_init
from ..namespace import NameBinding, BindingKind
from .send_chain import why_not_send, why_not_sync, render_chain
from ..type_def_registry import (
    is_fixed_int_type, is_fstr_type, int_traits_of,
    attach_dynamic_type_def, TypeCategory, EnumInfo, enum_info_of, type_def_of,
    factory_qnames_in_module, protocol_info_of, return_exception_marker,
    is_str_type, is_borrowing_view_type, is_owned_in_coro_frame,
    is_varargs,
)
from ..diagnostics import SemanticError
from .method_expansion import expand_methods_for_record
from .type_ops import signature_may_return_borrow
from ..value_category import iterator_source_callee
from .macros import run_macro_phase_for_record
from .operators import DUNDER_CPP_TEMPLATES
from ..macro_api import expr_to_cpp_default
from .literal_utils import const_expr_type, is_char_literal_init
from ..symbol_binding import (
    SymbolKind, install_binding, protocol_kind_for,
)

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from ..typesys import TypeRegistry

from tpyc import modules as builtin_modules
from .. import qnames

def _vararg_span_type(elem_type: 'TpyType') -> NominalType:
    """Build the sema-level body-view type for a *args parameter.

    Preserves the user's mutability intent in the element type: `*xs: T`
    lowers to varargs[T] (mutable vararg, codegen `varargs<T>`), `*xs: readonly[T]`
    to varargs[readonly[T]] (readonly vararg, codegen `varargs<const T>`). The
    element const-ness is the single source of truth for body mutability and
    for whether a readonly source may be unpacked into the slot. The varargs
    type is distinct from Span so a vararg is not accepted where a Span[T] is
    expected (no std::span conversion exists).
    """
    return make_varargs(elem_type)


def _is_valid_type_param_bound(t: 'TpyType') -> bool:
    """A bound usable on a type parameter: a protocol (capability or
    @dynamic), a sibling/enclosing type param (`U: T`), or a user-record
    class (`U: Animal`). The latter two are subtype bounds for
    representational coercion; the C++-upcast soundness they promise is
    enforced at instantiation, not at registration."""
    if is_protocol_type(t):
        return True
    if isinstance(t, TypeParamRef):
        return True
    return isinstance(t, NominalType) and t.is_user_record


_LINKAGE_MAP = {
    'DEFAULT': FunctionLinkage.DEFAULT,
    'NATIVE': FunctionLinkage.NATIVE,
    'NATIVE_C': FunctionLinkage.NATIVE_C,
    'EXPORT_C': FunctionLinkage.EXPORT_C,
}


def _contains_self_type(typ: TpyType) -> bool:
    """Check if a type contains SelfType anywhere in its structure."""
    if isinstance(typ, SelfType):
        return True
    return any(_contains_self_type(inner) for inner in typ.inner_types())


def _first_escaping_raise(stmts: list[TpyStmt]) -> TpyRaise | None:
    """The first `raise` in `stmts` that is not lexically under a `try`.

    Recurses through compound-statement bodies via `sub_bodies()` but stops
    at `try` (a raise there may be caught locally) and at nested `def`s
    (their bodies report empty `sub_bodies`, so they are skipped naturally).
    """
    for stmt in stmts:
        if isinstance(stmt, TpyRaise):
            return stmt
        if isinstance(stmt, TpyTry):
            continue
        for body in stmt.sub_bodies():
            found = _first_escaping_raise(body)
            if found is not None:
                return found
    return None


def _enum_default_matches_field(field_type: TpyType, member_info: EnumInfo) -> bool:
    """True if `field_type` admits an enum-member default of `member_info`.

    Walks Optional/Union wrappers so `c: Color | None = Color.RED` is still
    accepted; bare non-enum field types yield False. EnumInfo identity is
    canonical per enum (one TypeDef payload), so `is` distinguishes
    `Color` from a same-shaped sibling enum."""
    if enum_info_of(field_type) is member_info:
        return True
    return any(_enum_default_matches_field(inner, member_info)
               for inner in field_type.inner_types())


def check_enum_member_default(expr: TpyExpr, target_type: 'TpyType | None',
                              registry: 'TypeRegistry | None', loc: object,
                              *, target_noun: str) -> bool:
    """Validate a `Name.MEMBER` enum-member default against `target_type`.

    Returns True when `expr` is a member of a registered enum matching
    `target_type` (or `target_type` is None, e.g. a still-generic param slot).
    Raises on a missing member or an enum that mismatches `target_type`
    (codegen would otherwise emit a type-mismatched initializer C++ rejects
    opaquely). Returns False when `expr` is not an enum-member shape -- not
    `Name.attr`, or the base name is not a registered enum -- so the caller
    applies its own handling. Shared by field- and parameter-default
    validation so the two default surfaces stay consistent."""
    if registry is None or not (
            isinstance(expr, TpyFieldAccess) and isinstance(expr.obj, TpyName)):
        return False
    enum_t = registry.get_enum(expr.obj.name)
    if enum_t is None:
        return False
    info = enum_info_of(enum_t)
    if info is not None and expr.field in info.members:
        if target_type is not None and not _enum_default_matches_field(target_type, info):
            raise SemanticError(
                f"Default '{expr.obj.name}.{expr.field}' has enum type "
                f"'{expr.obj.name}', which does not match the declared "
                f"{target_noun} type '{target_type}'", loc)
        return True
    raise SemanticError(
        f"'{expr.field}' is not a member of enum '{expr.obj.name}'", loc)


def check_default_value_type(expr: TpyExpr, target_type: 'TpyType | None',
                             compat: 'TypeCompatibility | None', loc: object,
                             *, target_noun: str, target_name: str) -> None:
    """Type-check a constant default against the slot it initializes.

    The parser admits a default by SHAPE only ("is this a constant
    expression?"), so without this an ill-typed constant reaches codegen and
    renders through arms that cannot fail -- `n: int32 = None` emitting
    `int32_t n = nullptr`, or an out-of-range `int8 = 200` wrapping silently.
    Routed through the same compatibility check an assignment uses, so a
    default and its equivalent `x: T = <const>` agree on what is legal and
    report it in the same words.

    A generic target is skipped: a literal default on a `T`-typed slot is
    legitimately polymorphic and judged at instantiation. So is a default
    whose type `const_expr_type` cannot name -- an enum member or `Final[T]`
    name, each checked where its binding resolves.
    """
    if compat is None or target_type is None or contains_type_param(target_type):
        return
    actual = const_expr_type(expr)
    if actual is None:
        return
    context = f"{target_noun} '{target_name}'"
    # A fixed-int ctor carries its own range contract: `int8(200)` is out of
    # range whatever the slot is, so check the wrapped literal against the
    # ctor's type before the ctor's type against the slot.
    if isinstance(expr, TpyCall) and expr.args and is_fixed_int_type(actual):
        inner = const_expr_type(expr.args[0])
        if inner is not None:
            compat.check_type_compatible(inner, actual, context, loc)
    if is_char_literal_init(target_type, actual, expr):
        return
    compat.check_type_compatible(actual, target_type, context, loc)


def _validate_const_field_default(expr: TpyExpr, loc: object,
                                  registry: 'TypeRegistry | None' = None,
                                  field_type: TpyType | None = None,
                                  compat: 'TypeCompatibility | None' = None,
                                  field_name: str = "") -> None:
    """Validate that a field default expression is a compile-time constant."""
    if expr_to_cpp_default(expr) is not None:
        check_default_value_type(expr, field_type, compat, loc,
                                 target_noun="field", target_name=field_name)
        return
    # Enum members are constants: `c: Color = Color.RED` emits a C++ constant
    # initializer. Type-aware -- see check_enum_member_default.
    if check_enum_member_default(expr, field_type, registry, loc, target_noun="field"):
        return
    # Resolved call from a macro module (e.g. field()) used outside its macro
    if isinstance(expr, (TpyCall, TpyMethodCall)) and getattr(expr, 'resolved_import', None) is not None:
        mod, name = expr.resolved_import
        raise SemanticError(
            f"{mod}.{name}() can only be used in classes decorated with "
            f"a macro from '{mod}'", loc)
    raise SemanticError(
        "Default field value must be a constant expression "
        "(literal, None, or fixed-int constructor like int32(5))", loc)


def build_record_self_type(record: TpyRecord, qname: str | None = None) -> NominalType:
    """Build a NominalType representing Self for a record, preserving type param kinds.

    `qname` is the record's module-qualified name (from RecordInfo.qualified_name);
    when provided, the resulting NominalType carries `_module_qname` so the
    TypeDef-backed `is_user_record` resolves on `self`-typed references.
    """
    if record.type_params:
        type_args = tuple(
            TypeParamRef(
                name=tp,
                kind=record.type_param_kinds[i] if i < len(record.type_param_kinds) else TypeParamKind.TYPE,
            )
            for i, tp in enumerate(record.type_params)
        )
        return NominalType(record.name, type_args, _module_qname=qname)
    return NominalType(record.name, _module_qname=qname)


def receiver_self_type(record: 'TpyRecord | RecordInfo', registry) -> TpyType:
    """The type `self` and `Self` name in a method of `record`: the record's
    own Self type, or the enum for an enum's companion record."""
    if record.enum_companion_of is not None:
        enum_t = registry.get_enum(record.enum_companion_of)
        assert enum_t is not None, record.enum_companion_of
        return enum_t
    info = registry.get_record(record.name)
    return build_record_self_type(
        record, qname=info.qualified_name() if info is not None else None)


def _validate_dyn_dunder_kind(record: 'TpyRecord', dunder_name: str) -> object:
    """D16 Phase 4: reject decorator/kind forms that don't make sense for the
    dynamic-attribute dunders. Returns the method's source location (or
    `record.loc` when the dunder isn't on the class) so callers can reuse it
    without a second `record.methods` scan.
    """
    method = next((m for m in record.methods if m.name == dunder_name), None)
    if method is None:
        return record.loc
    loc = method.loc
    # @native records are owned by hand-written C++; mixing dyn-attr dunders
    # with native record shape is out of scope (see DYNAMIC_ATTRS_DESIGN.md).
    from ..parse import RecordLinkage
    if record.linkage != RecordLinkage.DEFAULT:
        raise SemanticError(
            f"{dunder_name} cannot be declared on @native records", loc)
    if method.is_staticmethod:
        raise SemanticError(f"{dunder_name} cannot be a @staticmethod", loc)
    if method.is_property_getter or method.is_property_setter:
        raise SemanticError(f"{dunder_name} cannot be a @property", loc)
    if method.is_overload_stub:
        raise SemanticError(
            f"{dunder_name} cannot be @{method.overload_form.value}", loc)
    if method.is_generator:
        raise SemanticError(f"{dunder_name} cannot be a generator (no `yield` in body)", loc)
    if method.error_return:
        raise SemanticError(f"{dunder_name} cannot use @error_return", loc)
    return loc


def _is_valid_dyn_getattr_return(ret: TpyType) -> bool:
    """D16 Phase 1: __getattr__ return type allow-list.

    Allowed: value types (primitives, char, str, BigInt, tuples, value-type
    user records), Any, or Own[T]. Bare reference types (non-value records,
    list/dict/set/bytes/bytearray) and views (Span/Ptr/Ref/StrView/BytesView)
    are rejected -- the dunder body computes a result with no place to borrow
    from.
    """
    if isinstance(ret, AnyType):
        return True
    if isinstance(ret, OwnType):
        return True
    if isinstance(ret, (PtrType, RefType)):
        return False
    if is_borrowing_view_type(ret):
        return False
    return ret.is_value_type()


class TypeRegistrar:
    """Registers builtin types, records, protocols, and functions."""

    def __init__(self, ctx: SemanticContext, type_ops: TypeOperations,
                 protocols: ProtocolChecker, compat: 'TypeCompatibility'):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self.compat = compat

    def _resolve_type_param_bounds(
        self, raw_bounds: dict[str, TpyType], loc,
        type_params: list[str] | None = None,
    ) -> dict[str, TpyType]:
        """Resolve parsed type parameter bounds, validating each is a
        protocol, a class, or a (sibling/enclosing) type parameter.

        When `type_params` is supplied (the declaration-order list of param
        names this bounds dict belongs to), bound TypeParamRef references to
        a not-yet-declared sibling are rejected here -- otherwise they slip
        through to `substitute_type_params` at a downstream use site and
        crash with a context-less "Unknown type parameter 'X'".
        """
        resolved: dict[str, TpyType] = {}
        if type_params is None:
            for param_name, bound_type in raw_bounds.items():
                resolved_bound = self.type_ops.resolve_type(bound_type)
                if not _is_valid_type_param_bound(resolved_bound):
                    raise SemanticError(
                        f"Type parameter bound must be a protocol, a class, or a "
                        f"type parameter, got {resolved_bound}",
                        loc,
                    )
                resolved[param_name] = resolved_bound
            return resolved
        # Declaration-order walk so a bound that names a sibling is checked
        # against the params declared so far. raw_bounds only contains bounded
        # params; the iteration drives off `type_params` so unbounded ones
        # still register their declaration position.
        declared_so_far: set[str] = set()
        for param_name in type_params:
            if param_name in raw_bounds:
                bound_type = raw_bounds[param_name]
                resolved_bound = self.type_ops.resolve_type(bound_type)
                if not _is_valid_type_param_bound(resolved_bound):
                    raise SemanticError(
                        f"Type parameter bound must be a protocol, a class, or a "
                        f"type parameter, got {resolved_bound}",
                        loc,
                    )
                self._reject_forward_typeparam_bound_refs(
                    param_name, resolved_bound, type_params, declared_so_far, loc)
                resolved[param_name] = resolved_bound
            declared_so_far.add(param_name)
        return resolved

    def _reject_forward_typeparam_bound_refs(
        self, param_name: str, bound: TpyType,
        type_params: list[str], declared_so_far: set[str], loc,
    ) -> None:
        sibling_set = set(type_params)
        forward = self._first_forward_typeparam_ref(bound, sibling_set, declared_so_far)
        if forward is not None:
            raise SemanticError(
                f"Type parameter '{param_name}' references '{forward}' in its "
                f"bound, but '{forward}' is declared later. Reorder so each "
                f"bound only names previously-declared type parameters.",
                loc,
            )

    def _first_forward_typeparam_ref(
        self, typ: TpyType, sibling_set: set[str], declared_so_far: set[str],
    ) -> str | None:
        if isinstance(typ, TypeParamRef):
            if typ.name in sibling_set and typ.name not in declared_so_far:
                return typ.name
            return None
        for inner in typ.inner_types():
            hit = self._first_forward_typeparam_ref(inner, sibling_set, declared_so_far)
            if hit is not None:
                return hit
        if isinstance(typ, NominalType) and typ.type_args:
            for arg in typ.type_args:
                if isinstance(arg, TpyType):
                    hit = self._first_forward_typeparam_ref(arg, sibling_set, declared_so_far)
                    if hit is not None:
                        return hit
        return None

    def register_tpy_star_import(self) -> None:
        """Register all tpy exports for 'from tpy import *'.

        Registers all exported types and functions from the tpy module
        into the global namespace and imported_names tracking.
        """
        # Register tpy types from type factories (int32, Array, Span, etc.)
        # Compile-time-only types (e.g. FStr) are also registered as type aliases
        # so the parser resolves them directly to their NominalType singleton.
        for qname in factory_qnames_in_module("tpy"):
            simple_name = bare_name(qname)
            if simple_name not in self.ctx.imported_names:
                self.ctx.imported_names[simple_name] = ("tpy", simple_name)
                self.ctx.global_ns.bind_imported_name(simple_name, "tpy", simple_name)
            self._register_compile_time_type_alias(simple_name, "tpy", simple_name)

        # Register compiled tpy module exports (functions, protocols, type aliases)
        tpy_info = self.ctx.registry.get_module("tpy")
        if tpy_info:
            for name in tpy_info.functions:
                if name not in self.ctx.imported_names:
                    self.ctx.imported_names[name] = ("tpy", name)
                    self.ctx.global_ns.bind_imported_name(name, "tpy", name)
            for name in tpy_info.protocols:
                if name not in self.ctx.imported_names:
                    self.ctx.imported_names[name] = ("tpy", name)
                    self.ctx.global_ns.bind_imported_name(name, "tpy", name)
            if tpy_info.type_aliases:
                for name in tpy_info.type_aliases:
                    if name not in self.ctx.imported_names:
                        self.ctx.imported_names[name] = ("tpy", name)
                        self.ctx.global_ns.bind_imported_name(name, "tpy", name)

    def _register_compile_time_type_alias(self, local_name: str, module: str, original_name: str) -> None:
        """Register a type alias for compile-time-only builtin types.

        Compile-time-only types (like FStr) have a type factory but no C++
        representation. They're resolved to their singleton at sema time so
        predicate checks (e.g. is_fstr_type(ptype)) work.
        Regular builtin types (int32, basic_slice, etc.) also flow as NominalType
        and use @native for C++ mapping.
        """
        type_obj = builtin_modules.get_builtin_type_obj(f"{module}.{original_name}")
        if type_obj is not None and type_obj.is_compile_time_only():
            self.ctx.registry.register_type_alias(local_name, type_obj)

    def get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def register_enum(self, enum: TpyEnum) -> None:
        """Register an enum type."""
        # Validate no duplicate member names
        seen_names: set[str] = set()
        for name, _, loc in enum.members:
            if name in seen_names:
                raise SemanticError(
                    f"Duplicate enum member name: '{name}'",
                    loc=loc or enum.loc,
                )
            seen_names.add(name)

        # Validate no duplicate values
        seen_values: dict[int, str] = {}
        for name, value, loc in enum.members:
            if value in seen_values:
                raise SemanticError(
                    f"Duplicate enum value {value} "
                    f"(already used by '{seen_values[value]}')",
                    loc=loc or enum.loc,
                )
            seen_values[value] = name

        # Determine underlying type
        underlying = INT32  # default
        if enum.is_int_enum and enum.underlying_type_name:
            underlying = self._resolve_int_enum_underlying(enum.underlying_type_name)
            # Validate member values fit in underlying type range
            underlying_tr = int_traits_of(underlying)
            if underlying_tr is not None:
                for name, value, loc in enum.members:
                    if value < underlying_tr.min_value or value > underlying_tr.max_value:
                        raise SemanticError(
                            f"Enum member '{name}' value {value} is out of range "
                            f"for {underlying} ({underlying_tr.min_value}..{underlying_tr.max_value})",
                            loc=loc or enum.loc,
                        )

        module_name = self.ctx.module_name if self.ctx.module_name != "__main__" else None
        members = tuple(m for m, _, _ in enum.members)
        member_values = tuple((m, v) for m, v, _ in enum.members)
        # Every enum gets a qname so it can be looked up in the TypeDef
        # registry.  For __main__ entry-point enums the `module_name`
        # field on EnumInfo stays None (codegen uses that to decide
        # whether to emit a namespace prefix) but the qname uses
        # "__main__" as its prefix so `type_def_of(t)` always finds the
        # entry.  `public_module_name` collapses private submodules
        # (e.g. `tpy._core._X` -> `tpy`) so the qname matches the
        # parser-side enum placeholder's qname for same-module refs.
        qname_module_raw = module_name if module_name is not None else "__main__"
        qname_module = public_module_name(qname_module_raw, self.ctx.module_cpp_namespace)
        qname = f"{qname_module}.{enum.name}"
        enum_type = NominalType(name=enum.name, type_args=(), _module_qname=qname)
        self.ctx.registry.register_enum(enum_type)
        self.ctx.global_ns.bind_enum(enum_type)
        install_binding(
            self.ctx.module_attributes, enum.name,
            SymbolKind.ENUM, enum_type,
        )
        # @native: normalize native_name to canonical global-scope form
        # (always ::-prefixed). Bare @native -> "::<short>"; explicit
        # @native("ns::E") -> "::ns::E"; already-prefixed left alone.
        native_name: str | None = None
        if enum.is_native:
            raw = enum.native_name or enum.name
            native_name = raw if raw.startswith("::") else f"::{raw}"

        cpp_member_names = tuple(
            (k, v) for k, v in enum.cpp_member_names.items()
        )
        enum_td = attach_dynamic_type_def(
            qname,
            TypeCategory.ENUM,
            enum=EnumInfo(
                members=members,
                member_values=member_values,
                underlying_type=underlying,
                is_int_enum=enum.is_int_enum,
                module_name=module_name,
                is_native=enum.is_native,
                native_name=native_name,
                cpp_member_names=cpp_member_names,
                has_explicit_values=enum.has_explicit_values,
            ),
            is_value_type=True,
        )
        # An `@export` enum crosses the CPython boundary as a value (its member's
        # int round-trips through the module's enum type); mark its TypeDef so
        # is_boundary_marshallable admits it as an @export param/return, the same
        # per-type fact exposed classes and scalars use.
        if enum.exposed_to_host:
            enum_td.boundary_marshal = True

    _INT_ENUM_UNDERLYING_MAP: dict[str, TpyType] = {
        "int": INT32,
        "int8": INT8, "int16": INT16, "int32": INT32, "int64": INT64,
        "uint8": UINT8, "uint16": UINT16, "uint32": UINT32, "uint64": UINT64,
    }

    def _resolve_int_enum_underlying(self, type_name: str) -> TpyType:
        """Resolve IntEnum underlying type name to TpyType."""
        result = self._INT_ENUM_UNDERLYING_MAP.get(type_name)
        if result is None:
            raise SemanticError(f"Unknown IntEnum underlying type: '{type_name}'")
        return result

    def _check_class_constant_conflicts(
        self, record: TpyRecord, class_constants: dict[str, FieldInfo],
        finality: dict[str, bool],
    ) -> None:
        """Reject class-constant names that collide with instance fields,
        methods, nested types on the same class, or with Final class
        constants on any ancestor. Non-final ClassVar redeclarations are
        permitted as Phase 8 shadows when the child's finality and type
        match the parent's (each declaring class gets its own
        `static inline` slot).

        Runs after `_partition_class_constants` so `record.fields` only holds
        instance fields. The ancestor walk is BFS over `record.bases` using
        already-registered parent `RecordInfo`s (parents are guaranteed to be
        registered first by the compiler-pipeline topological sort).
        """
        same_class_names: dict[str, str] = {}
        for fld in record.fields:
            same_class_names[fld.name] = "instance field"
        for method in record.methods:
            same_class_names.setdefault(method.name, "method")
        for nested in record.nested_records:
            same_class_names.setdefault(bare_name(nested.name), "nested record")
        for nested in record.nested_enums:
            same_class_names.setdefault(bare_name(nested.name), "nested enum")
        for cc_name, cc_fld in class_constants.items():
            kind = same_class_names.get(cc_name)
            if kind is not None:
                article = "an" if kind[:1] in "aeiou" else "a"
                raise SemanticError(
                    f"name '{cc_name}' on '{record.name}' is both a class constant "
                    f"and {article} {kind}",
                    loc=cc_fld.loc,
                )
        if not class_constants:
            return
        # Validate against every ancestor that declares the same name --
        # not just the nearest. Multi-base inheritance can have multiple
        # depth-1 parents declaring `X` (e.g. C(A, B) where A and B both
        # declare X independently); the shadow must satisfy *each* parent's
        # finality and type, otherwise C could silently override A's Final
        # by matching B first or skip B's type check entirely.
        cc_keys = class_constants.keys()
        seen_bases: set[str] = set()
        queue: list[NominalType] = [b for b in record.bases if isinstance(b, NominalType)]
        while queue:
            base = queue.pop(0)
            if base.name in seen_bases:
                continue
            seen_bases.add(base.name)
            parent = self.ctx.registry.get_record(base.name)
            if parent is None:
                continue
            for cc_name in cc_keys & parent.class_constants.keys():
                self._check_class_constant_shadow(
                    record, parent, cc_name, class_constants, finality,
                )
            for grandparent in parent.parents:
                if isinstance(grandparent, NominalType):
                    queue.append(grandparent)

    def _check_class_constant_shadow(
        self, record: TpyRecord, parent: RecordInfo, cc_name: str,
        class_constants: dict[str, FieldInfo], finality: dict[str, bool],
    ) -> None:
        """Validate that `record`'s declaration of `cc_name` is a permitted
        Phase 8 shadow of `parent`'s declaration. Final blocks override
        entirely; non-final ClassVar permits same-finality, same-type
        shadow (each class gets its own `static inline` slot, matching
        Python's per-`__dict__` shadowing semantics).
        """
        parent_final = parent.is_final_class_constant(cc_name)
        child_final = finality.get(cc_name, True)
        cc_fld = class_constants[cc_name]
        if parent_final:
            raise SemanticError(
                f"cannot override Final class constant '{cc_name}' "
                f"from base '{parent.name}'",
                loc=cc_fld.loc,
            )
        if child_final:
            raise SemanticError(
                f"cannot redeclare ClassVar '{cc_name}' from base "
                f"'{parent.name}' as Final; the child's read-only "
                f"declaration would conflict with the parent's mutable "
                f"storage",
                loc=cc_fld.loc,
            )
        parent_type = parent.class_constants[cc_name].type
        child_type = cc_fld.type
        if child_type != parent_type:
            raise SemanticError(
                f"ClassVar '{cc_name}' on '{record.name}' shadows base "
                f"'{parent.name}.{cc_name}' with incompatible type "
                f"'{child_type}' (expected '{parent_type}')",
                loc=cc_fld.loc,
            )

    def _extract_native_field_rename(self, fld: 'FieldInfo', is_native: bool) -> None:
        """Strip a `native_field("name")` call from `fld.default_expr` and
        record the rename on `fld.native_name`. Caller has already verified
        `fld.default_expr` is a TpyCall to `tpy.extern.native_field`.

        Validates that the surrounding class is `@native` and that the call's
        single positional argument is a string literal. Clears `default_expr`
        and `default_value` so downstream validation doesn't treat the call as
        a real initializer.
        """
        call = fld.default_expr
        assert isinstance(call, TpyCall)
        if not is_native:
            raise SemanticError(
                "native_field() is only allowed on @native classes",
                fld.loc,
            )
        if len(call.args) != 1 or call.kwargs:
            raise SemanticError(
                "native_field() takes exactly 1 positional string argument",
                fld.loc,
            )
        arg = call.args[0]
        if not isinstance(arg, TpyStrLiteral):
            raise SemanticError(
                "native_field() argument must be a string literal",
                fld.loc,
            )
        fld.native_name = arg.value
        fld.default_expr = None
        fld.default_value = None

    def _first_type_param_in_type(
        self, typ: TpyType, type_params: list[str],
    ) -> str | None:
        """Walk `typ`'s structure and return the first nested `TypeParamRef`
        whose name is in `type_params`, or None. Used by Phase 9 to verify
        that a generic class's class-constant declared type doesn't reference
        any of the class's type parameters.
        """
        if isinstance(typ, TypeParamRef) and typ.name in type_params:
            return typ.name
        for inner in typ.inner_types():
            found = self._first_type_param_in_type(inner, type_params)
            if found is not None:
                return found
        return None

    def _first_type_param_in_expr(
        self, expr: TpyExpr, type_params: list[str],
    ) -> str | None:
        """Return the first `type_params` entry (in declaration order) that
        appears as a `TpyName` anywhere in `expr`, or None. Used by Phase 9
        to verify that the initializer of a class constant on a generic
        class doesn't reference any of the class's type parameters
        (e.g. `Final[int32] = T()`). Iterating `type_params` rather than the
        set intersection keeps the error message deterministic across runs.
        """
        names = collect_name_refs(expr)
        return next((p for p in type_params if p in names), None)

    def _partition_class_constants(
        self, record: TpyRecord,
    ) -> tuple[dict[str, FieldInfo], dict[str, bool]]:
        """Split class-body Final/ClassVar annotations off `record.fields` into
        a class_constants dict (PEP 591 implicit-`ClassVar` rule for
        `Final[T] = value`; PEP 526 `ClassVar[T] = value` for mutable
        class-scoped storage).

        Mutates `record.fields` in place to drop routed entries so the rest of
        register_record (and downstream sema/codegen) only sees instance fields.
        Returns (class_constants, finality) -- the second dict maps each entry's
        name to True (Final, read-only, `static constexpr`) or False (mutable
        ClassVar, `static inline`).
        """
        is_native = record.linkage != RecordLinkage.DEFAULT
        is_native_c = record.linkage == RecordLinkage.NATIVE_C
        is_generic = bool(record.type_params)

        class_constants: dict[str, FieldInfo] = {}
        finality: dict[str, bool] = {}
        remaining: list[FieldInfo] = []
        partitioned_any = False
        for fld in record.fields:
            unwrapped = try_unwrap_class_constant(fld.type)
            if unwrapped is None:
                remaining.append(fld)
                continue
            inner_wrap, is_final = unwrapped
            partitioned_any = True

            if is_generic:
                # Phase 9: T-independent class constants on generic classes
                # are supported -- the inner type and initializer must not
                # reference any of the class's type parameters. T-dependent
                # forms (`Final[T]`, `Final[int32] = T()`) are deferred to
                # a future extension because they need per-monomorphization
                # codegen. The initializer check is name-based (collides
                # with type-param-shadowed globals): in C++ the template
                # parameter shadows the surrounding namespace inside the
                # template body, so a generic class's constant initializer
                # that *names* a type-param would emit invalid C++ even if
                # TPy's sema would resolve it to a module global.
                bad_param = self._first_type_param_in_type(inner_wrap, record.type_params)
                if bad_param is not None:
                    raise SemanticError(
                        f"class constant '{fld.name}' on generic class "
                        f"'{record.name}' references type parameter "
                        f"'{bad_param}' in its declared type; only "
                        f"T-independent class constants are supported",
                        loc=fld.loc,
                    )
                if fld.default_expr is not None:
                    bad_name = self._first_type_param_in_expr(fld.default_expr, record.type_params)
                    if bad_name is not None:
                        raise SemanticError(
                            f"class constant '{fld.name}' on generic class "
                            f"'{record.name}' references type parameter "
                            f"'{bad_name}' in its initializer; only "
                            f"T-independent class constants are supported "
                            f"(C++ template parameter shadows the same name "
                            f"in the surrounding namespace)",
                            loc=fld.loc,
                        )
            if is_native_c:
                raise SemanticError(
                    "`@native_c` classes have no static members; "
                    "declare a free `native_global` instead",
                    loc=fld.loc,
                )
            # Phase 10: `Final[T] = native_field("rename")` on @native class
            # constants -- treat as an extern binding (no TPy-side initializer)
            # with a per-symbol rename. Mirrors the instance-field native_field
            # handling at the top of register_record, but partitioning runs first
            # so we extract here for class constants. ClassVar on @native is
            # rejected below before this matters.
            if (is_final
                    and isinstance(fld.default_expr, TpyCall)
                    and fld.default_expr.resolved_import == ("tpy.extern", "native_field")):
                self._extract_native_field_rename(fld, is_native)
            if is_native:
                if not is_final:
                    raise SemanticError(
                        "ClassVar on @native classes is not supported; "
                        "TPy-owned mutable storage conflicts with C++-owned extern binding. "
                        "Use module-level `native_global` for a mutable extern static",
                        loc=fld.loc,
                    )
                if fld.default_expr is not None:
                    raise SemanticError(
                        "Final initializer conflicts with C++-owned storage; "
                        "use `Final[T]` without value to bind to an extern static, "
                        "or remove `@native` if you want TPy to own the constant",
                        loc=fld.loc,
                    )
            else:
                if fld.default_expr is None:
                    if is_final:
                        raise SemanticError(
                            "Final[T] without an initializer in a class body is not yet supported; "
                            "use `Final[T] = value` for a class constant",
                            loc=fld.loc,
                        )
                    raise SemanticError(
                        "ClassVar without an initializer is not supported; "
                        "use `ClassVar[T] = value`",
                        loc=fld.loc,
                    )
            # Final[str] -> StrView (string literals have static lifetime, so
            # constexpr storage is sound). For mutable ClassVar, mutation can
            # store a view into a temporary, so str/StrView are rejected --
            # don't rewrite either.
            if is_final:
                inner = final_type_str_to_strview(inner_wrap)
                if not is_final_allowed_inner(inner):
                    raise SemanticError(
                        f"Final[{inner}] is not supported; {FINAL_INNER_TYPE_ERROR}",
                        loc=fld.loc,
                    )
            else:
                inner = inner_wrap
                if not is_classvar_allowed_inner(inner):
                    raise SemanticError(
                        f"ClassVar[{inner}] is not supported; {CLASSVAR_INNER_TYPE_ERROR}",
                        loc=fld.loc,
                    )
            if fld.name in class_constants:
                raise SemanticError(
                    f"Duplicate class constant '{fld.name}' in '{record.name}'",
                    loc=fld.loc,
                )
            class_constants[fld.name] = FieldInfo(
                name=fld.name,
                type=inner,
                default_value=fld.default_value,
                default_expr=fld.default_expr,
                is_factory_default=fld.is_factory_default,
                loc=fld.loc,
                native_name=fld.native_name,
            )
            finality[fld.name] = is_final
        if partitioned_any:
            record.fields[:] = remaining
        return class_constants, finality

    def _set_generator_yield_type(self, func: TpyFunction) -> None:
        """Set generator_yield_type for a generator whose signature is not
        processed by register_function (the @overload impl is not callable, so
        it never reaches that pass) -- body analysis of its `yield`s asserts the
        field is present.
        """
        resolved_return = self.type_ops.resolve_type(func.return_type)
        if not (is_protocol_type(resolved_return)
                and resolved_return.qualified_name() == "typing.Iterator"):
            raise SemanticError(
                f"Generator function must have return type 'Iterator[T]', "
                f"got '{resolved_return}'",
                func.loc,
            )
        if not resolved_return.type_args:
            raise SemanticError(
                f"Iterator must have a type argument, e.g. Iterator[int32]",
                func.loc,
            )
        func.generator_yield_type = resolved_return.type_args[0]
        self._validate_generator_yield_copyable(func.generator_yield_type, func.loc)

    def _validate_generator_yield_copyable(self, yield_type: TpyType, loc) -> None:
        """Reject a generator whose VALUE-slot yield would copy a non-copyable value.

        Only a VALUE-ABI element is stored by value and handed out as a copy by
        `__next__()` -- there a `@nocopy` / `__del__` element would hit a deleted
        copy constructor. Under BORROW_REF the slot is `val_or_ref<T>` (a borrow,
        no copy) and under OWNED the slot moves out, so a non-copyable element is
        fine for both -- the per-yield rooting check in `_analyze_yield` enforces
        those instead. This is the declaration-driven yield-ABI rule (the element
        type drives the slot form, mirroring function returns)."""
        # Only a non-Own value-type element copies into the slot; an Own[T] yield
        # moves and a non-value yield borrows, so neither needs a copy ctor.
        if (isinstance(unwrap_readonly(unwrap_ref_type(yield_type)), OwnType)
                or not yield_type.is_value_type()):
            return
        if not self.ctx.is_type_non_copyable(yield_type):
            return
        reason = (self.ctx.nocopy_reason(yield_type)
                  if self.ctx.is_type_nocopy(yield_type)
                  else f"non-copyable type '{yield_type}'")
        raise SemanticError(
            f"Generator cannot yield {reason}: each yielded value is stored by "
            f"value and handed out as a copy by __next__(), which requires a "
            f"copy constructor. Yield a copyable type, or a tuple whose "
            f"elements are yielded by reference.",
            loc,
        )

    def _strip_interior_field_markers(self, record: TpyRecord) -> None:
        """Lower `unsafe_interior_mutable[Ptr[T]]` field annotations to a FieldInfo flag.

        `unsafe_interior_mutable[...]` is an unsafe escape hatch from the readonly
        boundary; only `unsafe_interior_mutable[Ptr[T]]` on a field is meaningful (the
        refcount-cell pattern). Reject the forms that would silently widen the
        hatch -- nested markers, readonly/Own inside, or a non-pointer payload -- so
        misuse fails at the declaration with a clear message rather than
        surfacing as wrong const-ness later.
        """
        for fld in record.fields:
            if not isinstance(fld.type, InteriorMutableType):
                continue
            inner = fld.type.wrapped
            if isinstance(inner, InteriorMutableType):
                raise SemanticError(
                    f"unsafe_interior_mutable[unsafe_interior_mutable[...]] on field "
                    f"'{fld.name}' is redundant",
                    loc=fld.loc,
                )
            if isinstance(inner, (ReadonlyType, OwnType)):
                raise SemanticError(
                    f"unsafe_interior_mutable[...] on field '{fld.name}' cannot wrap "
                    f"'{inner}'; unsafe_interior_mutable applies to a plain Ptr[T] field",
                    loc=fld.loc,
                )
            if not isinstance(inner, PtrType):
                raise SemanticError(
                    f"unsafe_interior_mutable[...] on field '{fld.name}' is only "
                    f"supported on a Ptr[T] field, not '{inner}'",
                    loc=fld.loc,
                )
            fld.type = inner
            fld.is_interior_mutable = True

    def _validate_instance_field(
        self, fld: FieldInfo, record: TpyRecord, is_generic: bool,
    ) -> None:
        """Validate one instance field's type and persist its resolved form.

        Shared by `register_record` (declared fields) and the subclass
        `__init__`-field inference in `validate_record_inheritance` (fields
        added after that pass), so inferred fields get the same INT-type-param
        / redundant-Own / protocol / Self / Fn rejections and the resolve_type
        write-back rather than slipping through to ill-formed C++.
        """
        # Check INT type params before general validation (to provide field location)
        if isinstance(fld.type, TypeParamRef) and fld.type.kind == TypeParamKind.INT:
            raise SemanticError(
                f"Integer type parameter '{fld.type.name}' cannot be used as a type annotation",
                loc=fld.loc
            )
        # A field owns its value inline regardless, so Own on a field is
        # redundant -- including `Optional[Own[T]]`, since a field
        # `Optional[T]` is `std::optional<T>` either way (the borrow default
        # that makes the exemption load-bearing exists only for locals).
        if type_contains_own(fld.type, allow_optional_own=False):
            raise SemanticError(
                f"Own[T] is redundant in this field type ('{fld.type}'): a "
                f"field owns its value inline -- remove the Own.",
                loc=fld.loc
            )
        # Hashable-conformance check is deferred to the second-pass
        # validate_record_field_protocols: records defined later in the
        # same module aren't fully registered yet (no methods), so a
        # field `dict[KeyDefinedLater, V]` would false-reject here.
        self.type_ops.validate_type(
            fld.type, allow_type_param_ref=is_generic, loc=fld.loc,
            check_hashable_constraints=False,
        )
        # Persist the resolved type back so downstream sema/codegen sees
        # parser-level NominalType placeholders substituted with registered
        # enums / protocol-flagged types / resolved aliases.
        resolved_fld_type = self.type_ops.resolve_type(fld.type)
        fld.type = resolved_fld_type
        # Protocol types cannot be used as field types
        if is_protocol_type(resolved_fld_type):
            raise SemanticError(
                f"Protocol type '{fld.type}' cannot be used as a field type in '{record.name}'. "
                f"Protocols are only valid as function and method parameters",
                loc=fld.loc
            )
        # Self cannot be used as a field type (infinite size or broken codegen)
        if _contains_self_type(fld.type):
            raise SemanticError(
                f"Self cannot be used as a field type in '{record.name}'",
                loc=fld.loc
            )
        if contains_fn_type(fld.type):
            raise SemanticError(
                "Fn type is only valid in parameter position. "
                "Use Callable for fields, returns, and locals",
                loc=fld.loc
            )

    def register_record(self, record: TpyRecord) -> None:
        """Register a record type."""
        is_native = record.linkage != RecordLinkage.DEFAULT
        is_native_c = record.linkage == RecordLinkage.NATIVE_C

        # @virtual_raise promises a hand-written dispatching C++ __raise__,
        # which only an @native class can supply -- plain TPy classes get the
        # auto-emitted `throw *this` override and cannot define their own,
        # so the marker would be accepted-then-meaningless there.
        if record.virtual_raise and record.linkage != RecordLinkage.NATIVE:
            raise SemanticError(
                f"@virtual_raise on '{record.name}' requires @native: it marks "
                f"a hand-written C++ __raise__ as dispatching, and a plain TPy "
                f"class cannot define one",
                record.loc)

        # For generic records, skip validation of TypeParamRef types
        is_generic = bool(record.type_params)

        # For non-@builtin_type user records, pre-register a minimal RecordInfo
        # (name + module + type_params) so that resolve_type's user-record
        # substitution can mint `_module_qname` on references to the class by
        # name inside its own methods/fields (e.g. `def __iter__(self) ->
        # Counter` or `def push(self, x: T) -> Stack[T]`) even while the full
        # info is still being built. type_params is required so validate_type's
        # arity check recognizes `Stack[T]` as a valid generic reference. The
        # full info gets registered below and overwrites this stub.
        # @builtin_type stubs (list, dict, ...) are skipped -- the parser has
        # already registered those.
        #
        # Direct assignment to `registry.records[...]` (not via the
        # `register_record()` method) is intentional: `register_record()` also
        # writes `_qname_index` for `builtin_type_key`-carrying infos, and we
        # don't want the placeholder to pollute that index. The full
        # registration at the bottom of this function goes through the normal
        # path. If `register_record()` grows new side effects, update the
        # comment here so the skip is still audited.
        if not record.builtin_type_key:
            record_module = public_module_name(self.ctx.module_name, self.ctx.module_cpp_namespace) or None
            # If `_pre_populate_decl_exports` minted a skeleton
            # RecordInfo for this record (so cycle peers' bind_imports
            # could find it before our sub-phase 2 ran), adopt that
            # skeleton as the placeholder. The full registration at
            # the bottom mutates the same object so peer registries
            # see the freshly-finalized data.
            placeholder: RecordInfo
            decl_exports = self.ctx.module_decl_exports
            existing_skeleton = (decl_exports.records.get(record.name)
                                 if decl_exports is not None else None)
            if existing_skeleton is not None:
                placeholder = existing_skeleton
                placeholder.module = record_module
                placeholder.defining_module = self.ctx.module_name
                placeholder.type_params = (list(record.type_params)
                                           if record.type_params else [])
                placeholder.type_param_kinds = (list(record.type_param_kinds)
                                                if record.type_param_kinds else [])
            else:
                placeholder = RecordInfo(
                    name=record.name,
                    fields=[],
                    module=record_module,
                    defining_module=self.ctx.module_name,
                    type_params=list(record.type_params) if record.type_params else [],
                    type_param_kinds=list(record.type_param_kinds) if record.type_param_kinds else [],
                )
            self.ctx.registry.records[record.name] = placeholder

        # Partition class-body Final/ClassVar annotations into class_constants
        # (PEP 591 implicit-ClassVar rule for `Final[T] = value`; PEP 526
        # `ClassVar[T] = value` for mutable storage). The remainder of
        # register_record only sees instance fields.
        class_constants, class_constants_finality = self._partition_class_constants(record)
        self._check_class_constant_conflicts(record, class_constants, class_constants_finality)

        self._strip_interior_field_markers(record)

        # Validate field types
        for fld in record.fields:
            self._validate_instance_field(fld, record, is_generic)

        # __await__ on user types: rejected as "not yet supported" per
        # docs/ASYNC_DESIGN.md's v1 sema exclusions. v3+ may support
        # user-defined __await__ adaptation for CPython interop.
        for m in record.methods:
            if m.name == "__await__":
                raise SemanticError(
                    f"user-defined '__await__' is not yet supported "
                    f"(method on '{record.display_name}'); v1 only supports the "
                    f"structural Awaitable protocol via `poll(self, "
                    f"waker: Waker) -> Own[Poll[T]]`",
                    m.loc or record.loc,
                )

        del_method = record.del_method
        if del_method:
            if del_method.params:
                raise SemanticError(
                    f"'__del__' must not have parameters",
                    del_method.loc or record.loc,
                )
            if not isinstance(del_method.return_type, VoidType):
                raise SemanticError(
                    f"'__del__' must return None, got '{del_method.return_type}'",
                    del_method.loc or record.loc,
                )
            if del_method.is_staticmethod:
                raise SemanticError(
                    f"'__del__' cannot be a static method",
                    del_method.loc or record.loc,
                )
            if del_method.type_params:
                raise SemanticError(
                    f"'__del__' cannot have type parameters",
                    del_method.loc or record.loc,
                )
            # A raise that escapes __del__ terminates the process: the body runs
            # in a `noexcept` destructor, so it cannot propagate. Warn rather
            # than reject -- it is valid Python (CPython prints + ignores it),
            # and the with/try fail-fast wrap aborts it anyway if reached. Only
            # an un-try-guarded raise is flagged (one under a `try` may be
            # caught locally); indirect throws need nothrow tracking we lack.
            escaping = _first_escaping_raise(del_method.body)
            if escaping is not None:
                self.ctx.warning(
                    "'raise' in '__del__' cannot propagate: a destructor has no "
                    "caller to receive the exception, so reaching it terminates "
                    "the process -- move the raising code out of '__del__'",
                    escaping,
                )

        move_method = record.move_method
        if move_method:
            # Codegen inlines __move__ into the move ctor and marks the source
            # moved-from via the __tpy_owned_ drop flag, which only exists for
            # classes with __del__; without it the body would be silently
            # dropped (default member-wise move used instead).
            if del_method is None:
                raise SemanticError(
                    f"'__move__' requires the class to define '__del__' "
                    f"(the relocating move pairs with custom destruction)",
                    move_method.loc or record.loc,
                )
            if len(move_method.params) != 1 or not isinstance(
                    move_method.params[0][1], OwnType):
                raise SemanticError(
                    f"'__move__' must take exactly one 'Own[Self]' parameter "
                    f"(the move source)",
                    move_method.loc or record.loc,
                )
            if not isinstance(move_method.return_type, VoidType):
                raise SemanticError(
                    f"'__move__' must return None, got "
                    f"'{move_method.return_type}'",
                    move_method.loc or record.loc,
                )
            if move_method.is_staticmethod:
                raise SemanticError(
                    f"'__move__' cannot be a static method",
                    move_method.loc or record.loc,
                )
            # The body is inlined into a noexcept move ctor, so a raise that
            # reaches it std::terminates. A raise lexically under a `try` may
            # be caught locally, so only an un-try-guarded one is rejected;
            # indirect throws (a callee that raises) need nothrow tracking we
            # don't have yet.
            escaping = _first_escaping_raise(move_method.body)
            if escaping is not None:
                raise SemanticError(
                    f"'__move__' must not raise: its body runs inside a "
                    f"'noexcept' move constructor, so a raise terminates the "
                    f"program",
                    escaping.loc or move_method.loc or record.loc,
                )

        # Macro phase: apply class macros (@dataclass, @model, ...)
        # then resolve any TypeRefNodes in macro-added method bodies.
        # See `sema/macros.py` for why this runs inside sema.
        run_macro_phase_for_record(record, self.ctx)

        # Expand methods on the final method set (source + macro-added).
        # Runs self-flag derivation, validation, @auto_readonly / property
        # setter wrapping, and cloning.  Idempotent on already-expanded
        # clones, so running once per record here is safe.
        expand_methods_for_record(record)

        # Check for duplicate method definitions (second definition silently wins in Python,
        # but it is always a bug and can interfere with @override checks).
        # Runs post-expansion so the clone flags correctly exempt clone
        # pairs. @overload stubs are exempt -- multiple stubs + one
        # implementation share the same name. Property getter+setter share
        # a name -- also exempt.
        propagate_clone_names: set[str] = {m.name for m in record.methods if m.is_auto_readonly_mutable_clone or m.is_auto_own_borrowing_clone}
        overload_names: set[str] = {m.name for m in record.methods if m.is_overload_stub}
        property_method_names: set[str] = {m.name for m in record.methods if m.is_property_getter or m.is_property_setter}
        seen_method_names: set[str] = set()
        for method in record.methods:
            if method.name in overload_names or method.name in propagate_clone_names:
                continue
            if method.name in property_method_names:
                continue
            if method.name in seen_method_names:
                raise SemanticError(
                    f"Method '{method.name}' defined twice in class '{record.display_name}'",
                    method.loc or record.loc,
                )
            seen_method_names.add(method.name)

        # Extract native_field() renames on @native classes. The call is not a
        # real default -- it's stripped here so the const validation below
        # doesn't see it. Class-constant fields are handled separately in
        # `_partition_class_constants` (which runs earlier, before macros).
        for fld in record.fields:
            if not isinstance(fld.default_expr, TpyCall):
                continue
            if fld.default_expr.resolved_import != ("tpy.extern", "native_field"):
                continue
            self._extract_native_field_rename(fld, is_native)

        # Validate field defaults are const (after macros have transformed them)
        for fld in record.fields:
            if fld.default_expr is not None and not fld.is_factory_default:
                _validate_const_field_default(
                    fld.default_expr, fld.loc, self.ctx.registry, fld.type,
                    self.compat, fld.name)

        init_params = []
        if record.init_method:
            init_defaults = record.init_method.defaults
            for i, (pname, ptype) in enumerate(record.init_method.params):
                has_default = bool(init_defaults) and i < len(init_defaults) and init_defaults[i] is not None
                resolved_ptype = self.type_ops.resolve_type(ptype, protocols_only=True)
                init_params.append((pname, resolved_ptype, init_defaults[i] if has_default else None))
        elif is_native and record.fields:
            # Native records without __init__: synthesize init_params from fields.
            # Uses default_value (raw C++ literal string like "0", "nullptr") -- these
            # go directly into struct field declarations, not through kwargs resolution.
            for fld in record.fields:
                init_params.append((fld.name, fld.type, fld.default_value))
        elif record.is_typed_dict and record.fields:
            # total=False: wrap all field types in Optional, set None as default
            if record.is_total_false:
                for fld in record.fields:
                    if not isinstance(fld.type, OptionalType):
                        fld.type = OptionalType(fld.type)
                    if fld.default_expr is None:
                        fld.default_expr = TpyNoneLiteral(loc=fld.loc)
                        fld.default_value = "std::nullopt"
            # Synthesize init_params from fields (all keyword-constructible).
            # total=True: all required (no defaults). total=False: Optional with None default.
            for fld in record.fields:
                default = fld.default_expr if record.is_total_false else None
                init_params.append((fld.name, fld.type, default))

        # Build the Self type for this record (used to substitute SelfType in methods)
        record_self_type = receiver_self_type(record, self.ctx.registry)

        # Pre-compute ids of mutable clones from @auto_readonly (flagged by the parser).
        # Used below to prevent implicit_readonly from clobbering is_readonly=False on these
        # methods, which would break mutable-vs-const overload tie-breaking.
        mutable_clone_ids: set[int] = {id(m) for m in record.methods
                                       if m.is_auto_readonly_mutable_clone or m.is_auto_own_borrowing_clone}

        # Resolve record-level type param bounds early so that
        # attach_type_param_bounds in the method loop below uses resolved versions
        # (with is_protocol=True, _module_qname set from sema registry).
        if record.type_param_bounds:
            resolved_record_bounds: dict[str, NominalType] = {}
            for param_name, bound_type in record.type_param_bounds.items():
                resolved_bound = self.type_ops.resolve_type(bound_type) if not is_protocol_type(bound_type) else bound_type
                if not is_protocol_type(resolved_bound):
                    raise SemanticError(
                        f"Type parameter bound must be a protocol, got {resolved_bound}",
                        record.loc,
                    )
                resolved_record_bounds[param_name] = resolved_bound
            record.type_param_bounds.update(resolved_record_bounds)

        # Register all methods
        methods = {}
        # Parallel map of method-name -> source nodes, in registration order.
        # Used by `_reject_same_param_overloads` to anchor diagnostics at the
        # offending method's location.
        method_nodes: dict[str, list] = {}
        # Compute the record's qname once so method FunctionInfos can carry it
        # (enables qname-based identification at codegen, e.g. peephole folds).
        if record.builtin_type_key:
            owning_type_qname = record.builtin_type_key
        else:
            _mod = public_module_name(self.ctx.module_name, self.ctx.module_cpp_namespace) or self.ctx.module_name
            owning_type_qname = f"{_mod}.{record.name}"
        for method in record.methods:
            method_has_type_params = bool(method.type_params)
            allow_tpref = is_generic or method_has_type_params

            # Validate Self usage: not allowed in @staticmethod. A @classmethod
            # is exempt -- `cls` binds it to the defining record, which the
            # substitution below resolves.
            if method.is_staticmethod and not method.is_classmethod:
                for pname, ptype in method.params:
                    if _contains_self_type(ptype):
                        raise SemanticError(
                            f"Self type cannot be used in @staticmethod '{record.display_name}.{method.name}' "
                            f"parameter '{pname}'",
                            method.loc or record.loc,
                        )
                if _contains_self_type(method.return_type):
                    raise SemanticError(
                        f"Self type cannot be used as return type of "
                        f"@staticmethod '{record.display_name}.{method.name}'",
                        method.loc or record.loc,
                    )
            # Substitute Self -> record type and resolve cross-module protocol flags
            method_params = [
                (n, self.type_ops.resolve_type(
                    self.type_ops.substitute_self(t, record_self_type), protocols_only=True))
                for n, t in method.params
            ]
            method_return = self.type_ops.resolve_type(
                self.type_ops.substitute_self(method.return_type, record_self_type), protocols_only=True)

            for pname, ptype in method_params:
                if has_auto_readonly(ptype):
                    raise SemanticError(
                        f"Internal error: unresolved 'auto_readonly[T]' in parameter "
                        f"'{pname}' of '{record.display_name}.{method.name}'",
                        method.loc or record.loc,
                    )
                if not contains_type_param(ptype):
                    # Hashable check deferred (see field-validation comment
                    # above): sibling records aren't fully registered yet.
                    self.type_ops.validate_type(
                        ptype, allow_type_param_ref=allow_tpref, loc=record.loc,
                        check_hashable_constraints=False,
                    )
            if not contains_type_param(method_return):
                self.type_ops.validate_type(
                    method_return, allow_type_param_ref=allow_tpref, loc=record.loc,
                    check_hashable_constraints=False,
                )
            # Protocol types cannot be used as method return types.
            # Exceptions: @dynamic protocols, and @native/@cpp_template stub methods
            # (C++ handles the actual return type).
            if is_protocol_type(method_return):
                pi = protocol_info_of(method_return)
                is_native_stub = method.is_stub and (method.native_name or method.cpp_template)
                # __iter__ returns Iterator[T] which is a protocol -- allow it since
                # C++ codegen uses auto return type (deduced from body).
                is_iter_method = method.name == "__iter__" and isinstance(method_return, NominalType) and method_return.qualified_name() in (qnames.ITERATOR, qnames.ITERABLE)
                if not (pi and pi.is_dynamic) and not is_native_stub and not is_iter_method and not method.is_generator:
                    raise SemanticError(
                        f"Protocol type '{method_return.name}' cannot be used as a return type in '{record.display_name}.{method.name}'. "
                        f"Only @dynamic protocols can be used as return types",
                        method.loc or record.loc,
                    )
            # Generator method: extract yield type from Iterator[T] return type
            if method.is_generator:
                if not (is_protocol_type(method_return) and isinstance(method_return, NominalType)
                        and method_return.qualified_name() == "typing.Iterator"):
                    raise SemanticError(
                        f"Generator method must have return type 'Iterator[T]', "
                        f"got '{method_return}'",
                        method.loc or record.loc,
                    )
                if not method_return.type_args:
                    raise SemanticError(
                        f"Iterator must have a type argument, e.g. Iterator[int32]",
                        method.loc or record.loc,
                    )
                method.generator_yield_type = method_return.type_args[0]
                self._validate_generator_yield_copyable(
                    method.generator_yield_type, method.loc or record.loc)
            # For the mutable clone of a auto_readonly pair, skip implicit_readonly so
            # that the mutable clone keeps is_readonly=False. This allows tie-breaking in
            # method resolution to correctly distinguish the two clones based on receiver
            # const-ness. Without this, implicitly-readonly methods like __span__ would have
            # both clones marked is_readonly=True, making tie-breaking pick the wrong clone.
            is_mutable_propagate_clone = id(method) in mutable_clone_ids
            is_implicit_readonly = (
                method.name in IMPLICIT_READONLY_METHODS
                and not method.readonly_opt_out
                and not is_mutable_propagate_clone
            )
            # @pure implies readonly, but never for the mutable clone of an
            # auto_readonly pair -- the clone hands out a mutable borrow, so
            # its receiver must stay non-const (same reason as the
            # implicit_readonly exemption).
            resolved_readonly = (method.is_readonly
                or (method.is_pure and not is_mutable_propagate_clone)
                or is_implicit_readonly
                or (record.is_frozen and method.name != "__init__"))
            method.is_readonly = resolved_readonly
            method_type_param_bounds = self._resolve_type_param_bounds(
                method.type_param_bounds, method.loc or record.loc,
                type_params=list(method.type_params))
            if method_type_param_bounds:
                method.type_param_bounds.update(method_type_param_bounds)
            method_defaults = method.defaults if method.defaults else []
            # Propagate resolved types back to AST so analyzer/codegen see concrete types.
            # Attach class-level type param bounds to TypeParamRef instances so that
            # codegen can check bounds (e.g. T: ValueType) without context lookup.
            if record.type_param_bounds:
                method_params = [
                    (n, attach_type_param_bounds(t, record.type_param_bounds))
                    for n, t in method_params
                ]
                method_return = attach_type_param_bounds(method_return, record.type_param_bounds)
            # Validate that auto_readonly[T] in return type is only on @auto_readonly methods.
            # With parser cloning, clones always have auto_readonly=False and their return types
            # already have AutoReadonlyType resolved. If a user writes auto_readonly[T]
            # without the decorator, it's an error.
            if has_auto_readonly(method_return):
                raise SemanticError(
                    f"'auto_readonly[T]' in return type is only allowed on "
                    f"@auto_readonly methods ('{record.display_name}.{method.name}')",
                    method.loc or record.loc,
                )
            if has_auto_own(method_return):
                raise SemanticError(
                    f"'auto_own[T]' in return type is only allowed on "
                    f"auto_own[Self] methods ('{record.display_name}.{method.name}')",
                    method.loc or record.loc,
                )
            method.params = method_params
            method.return_type = method_return
            # Inplace dunders must return self (the record type), not None or Own[T]
            if method.name in CONST_PARAMS_METHODS:
                if isinstance(method_return, OwnType):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.display_name}'), "
                        f"not Own[{method_return.wrapped}] -- inplace methods return self, not a new value",
                        method.loc or record.loc,
                    )
                if isinstance(method_return, VoidType):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.display_name}'), not None",
                        method.loc or record.loc,
                    )
                if not (isinstance(method_return, NominalType) and method_return.name == record.name):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.display_name}'), "
                        f"got '{method_return}'",
                        method.loc or record.loc,
                    )
            kw_start = method.keyword_only_start
            method_param_infos = [
                ParamInfo(n, t,
                          default_expr=method_defaults[i] if i < len(method_defaults) else None,
                          keyword_only=(kw_start is not None and i >= kw_start),
                          positional_only=i < method.num_posonly_params)
                for i, (n, t) in enumerate(method_params)
            ]
            # *args goes before keyword-only params so call-site arg packing
            # (`_analyze_and_pack_varargs`) sees the canonical
            # [fixed..., variadic, kwonly...] layout shared with free functions.
            if method.vararg_name is not None and method.vararg_type is not None:
                # A variadic coroutine factory's await-call lowering is not
                # wired and miscompiles to opaque C++; reject at the source.
                if method.is_async:
                    raise SemanticError(
                        "Variadic positional parameters (*args) are not yet "
                        "supported on async methods",
                        method.loc or record.loc
                    )
                va_type = self.type_ops.resolve_type(method.vararg_type)
                va_param = ParamInfo(method.vararg_name, _vararg_span_type(va_type),
                                     is_variadic=True)
                if kw_start is not None and kw_start < len(method_param_infos):
                    method_param_infos.insert(kw_start, va_param)
                else:
                    method_param_infos.append(va_param)
            if method.kwarg_name is not None and method.kwarg_type is not None:
                resolved_kwarg_type = self.type_ops.resolve_type(method.kwarg_type)
                method_param_infos.append(ParamInfo(method.kwarg_name, resolved_kwarg_type,
                                                    is_kwargs=True))
            self._validate_param_defaults(method_param_infos)
            # async def method: callers see Cancellable[T]. Mirrors free async def
            # (line ~2603); the user's T stays on method.return_type for codegen
            # and the body-return checker. Cancellable structurally extends
            # Awaitable, so `await` / `async for` / `async with` still match.
            if method.is_async:
                fi_method_return = make_cancellable(
                    NONE if isinstance(method_return, VoidType) else method_return)
            else:
                fi_method_return = method_return
            func_info = FunctionInfo(
                name=method.name,
                params=method_param_infos,
                return_type=fi_method_return,
                async_inner_return=method_return if method.is_async else None,
                is_readonly=resolved_readonly,
                is_pure=method.is_pure,
                is_inline=method.is_inline,
                is_consuming=method.is_consuming,
                is_method=True,
                is_async=method.is_async,
                is_generator=method.is_generator,
                send_override=method.send_override,
                sync_override=method.sync_override,
                is_staticmethod=method.is_staticmethod,
                is_classmethod=method.is_classmethod,
                is_property_getter=method.is_property_getter,
                is_property_setter=method.is_property_setter,
                property_name=method.property_name,
                linkage=method.linkage,
                native_name=method.native_name,
                native_function=method.native_function,
                native_preserves_refs=method.native_preserves_refs,
                copy_returns_warn=method.copy_returns_warn,
                # Only `__enter__` needs it, and only it pays the body walk.
                returns_self_borrow=(
                    returns_borrow_rooted_at_self(method)
                    if method.name == '__enter__' else True),
                native_cpp_return_type=method.native_cpp_return_type,
                cpp_template=method.cpp_template or (DUNDER_CPP_TEMPLATES.get(method.name)
                             if not method.native_function else None),
                type_params=list(method.type_params),
                type_param_bounds=method_type_param_bounds,
                error_return_type=(qualify_exception_name(method.error_return, self.ctx.registry,
                                                          self.ctx.module_name)
                                   if method.error_return else None),
                kwarg_name=method.kwarg_name,
                owning_type_qname=owning_type_qname,
                is_auto_readonly_mutable_clone=method.is_auto_readonly_mutable_clone,
                originating_module=self.ctx.module_name,
            )
            if method.is_generator:
                # The frame also stores the receiver by reference, but -1 is
                # NOT stamped: return_borrows_from containing -1 blocks
                # readonly inference (a self-borrowing return pins non-const),
                # which would flip every generator method non-readonly; the
                # receiver borrow is tracked in BUGS.md instead.
                self._stamp_iterator_retention(method, func_info)
            elif ((func_info.native_name is not None
                       or func_info.is_native
                       or func_info.cpp_template is not None)
                    and method.is_stub
                    and not method.is_consuming
                    and not method.is_staticmethod
                    and signature_may_return_borrow(func_info)):
                # Body-less native methods get no body-derived borrow facts,
                # so derive the receiver borrow from the signature: a native
                # accessor returning a non-value, non-Own type hands out a
                # borrow of (or view into) its receiver (dict views,
                # __getitem__, get, setdefault). Readonly inference is not
                # affected -- builtin stubs declare readonly-ness explicitly.
                func_info.return_borrows_from = frozenset({-1})
            # @inline: store the body expression for call-site inlining.
            # Body must be a single call statement. Cloned and substituted at call sites.
            if method.is_inline and not method.is_stub:
                non_doc = [s for s in method.body
                           if not (isinstance(s, TpyExprStmt)
                                   and isinstance(s.expr, TpyStrLiteral))]
                if (len(non_doc) == 1
                        and isinstance(non_doc[0], TpyExprStmt)
                        and isinstance(non_doc[0].expr, (TpyCall, TpyMethodCall))):
                    func_info.inline_body = non_doc[0].expr
                else:
                    raise SemanticError(
                        f"@inline method '{record.display_name}.{method.name}' must have a single "
                        f"call expression as its body.",
                        method.loc or record.loc,
                    )
            # Validate: FStr params require @inline
            elif func_info.has_fstr_param and not method.is_stub:
                raise SemanticError(
                    f"Method '{record.display_name}.{method.name}' has FStr parameter but is "
                    f"not marked @inline. FStr parameters require @inline.",
                    method.loc or record.loc,
                )
            # Propagate qualified name back to AST so codegen can use it directly.
            # ReturnException validation is deferred to validate_method_error_returns()
            # because the ReturnException marker on exception records is set during
            # validate_record_inheritance, which runs after register_record.
            if method.error_return:
                method.error_return = func_info.error_return_type
            if method.is_property_getter or method.is_property_setter:
                # Property getter/setter share a name -- store both in the list
                methods.setdefault(method.name, []).append(func_info)
                method_nodes.setdefault(method.name, []).append(method)
            elif method.is_overload_stub:
                # Accumulate overload stubs for this method name
                methods.setdefault(method.name, []).append(func_info)
                method_nodes.setdefault(method.name, []).append(method)
            elif (method.name in methods
                  and method.name not in overload_names
                  and any(m.is_readonly != func_info.is_readonly
                          or m.is_consuming != func_info.is_consuming
                          for m in methods[method.name])):
                # auto_readonly or auto_own clone: add the complementary overload
                methods[method.name].append(func_info)
                method_nodes[method.name].append(method)
            elif method.name in methods:
                # Implementation following stubs: stubs are the callable
                # overloads. Don't add the implementation to the method list --
                # callers resolve against stubs only.
                pass
            else:
                methods[method.name] = [func_info]
                method_nodes[method.name] = [method]

        # Per-method same-param-diff-return check. Mirrors the free-function
        # check in register_overload_group, with the auto_readonly /
        # auto_own const-qualified variants exempted (they emit as
        # `&` / `const &` / `&&` qualified C++ overloads and are not
        # ambiguous). Property getter/setter pairs are excluded too --
        # they're a pair, not alternative overloads, and codegen emits
        # them under the property's distinct lvalue/rvalue paths.
        # Dynamic-attr dunders are excluded so their own
        # `_validate_dyn_dunder_kind` can produce a more specific
        # diagnostic ("__getattr__ cannot be @overload" / "@dispatch").
        _DYN_DUNDER_NAMES = ("__getattr__", "__setattr__", "__delattr__")
        for method_name, method_infos in methods.items():
            if len(method_infos) <= 1:
                continue
            if method_name in _DYN_DUNDER_NAMES:
                continue
            if any(fi.is_property_getter or fi.is_property_setter for fi in method_infos):
                continue
            self._reject_same_param_overloads(
                method_infos, method_nodes.get(method_name), is_method=True)

        # Auto-synthesize __iter__() -> Self on iterator types (has __next__ but no __iter__)
        if "__next__" in methods and "__iter__" not in methods:
            methods["__iter__"] = [FunctionInfo(
                name="__iter__",
                params=[],
                return_type=record_self_type,
                is_method=True,
                is_readonly=False,
                owning_type_qname=owning_type_qname,
            )]

        # Attach resolved bounds to TypeParamRef instances in RecordInfo method
        # signatures so downstream code (codegen, type_ops) can check bounds.
        if record.type_param_bounds:
            for method_list in methods.values():
                for i, func_info in enumerate(method_list):
                    new_params = [
                        dc_replace(p, type=attach_type_param_bounds(p.type, record.type_param_bounds))
                        for p in func_info.params
                    ]
                    new_return = attach_type_param_bounds(func_info.return_type, record.type_param_bounds)
                    if (any(np.type is not op.type for np, op in zip(new_params, func_info.params))
                            or new_return is not func_info.return_type):
                        method_list[i] = dc_replace(
                            func_info, params=new_params, return_type=new_return)

        # Validate __copy__ signature
        has_copy = "__copy__" in methods
        if has_copy:
            copy_loc = next((m.loc for m in record.methods if m.name == "__copy__"), record.loc)
            if record.is_nocopy:
                raise SemanticError(
                    f"@nocopy class '{record.name}' cannot define __copy__",
                    record.loc,
                )
            copy_info = methods["__copy__"][0]
            if len(copy_info.params) > 0:
                raise SemanticError(
                    f"__copy__ must take no parameters (besides self)",
                    copy_loc,
                )
            ret = copy_info.return_type
            inner = ret.wrapped if isinstance(ret, OwnType) else ret
            if not (isinstance(inner, NominalType) and inner.name == record.name):
                raise SemanticError(
                    f"__copy__ must return {record.name}, got {ret}",
                    copy_loc,
                )

        # Validate __exit__ signature (v1.5 M1). Parser already enforces the
        # 3-param shape; sema checks return + param types.
        #   - return type: `bool` (may suppress) or `None` (cleanup-only).
        #   - exc_type, exc_tb: `None` (carry no payload in v1.5).
        #   - exc_val: `None` (cleanup-only) or `Optional[BaseException]`
        #     (synthesized default for unannotated; lets the body inspect).
        # Other shapes are rejected so the codegen call-site doesn't need
        # to handle exotic param types.
        if "__exit__" in methods:
            exit_loc = next(
                (m.loc for m in record.methods if m.name == "__exit__"),
                record.loc,
            )
            # __exit__ is dispatched at a single codegen call site per
            # `with` item; multiple overloads would force runtime selection
            # that the call-site shape can't express. Reject up front.
            if len(methods["__exit__"]) > 1:
                raise SemanticError(
                    "__exit__ cannot be overloaded",
                    exit_loc,
                )
            exit_info = methods["__exit__"][0]
            # Canonical-signature hint appended to every __exit__
            # validation error so the user sees the complete expected
            # shape in one diagnostic instead of fixing slots in
            # round-trips. Return alternatives are spelled `-> bool`
            # vs `-> None` (not `bool | None`) since the user picks
            # one shape; the `exc_val: None` opt-out is noted briefly.
            exit_hint = (
                " (expected `def __exit__(self, exc_type: None, "
                "exc_val: BaseException | None, exc_tb: None) -> bool` "
                "for suppressing managers, or `-> None` for cleanup-only; "
                "`exc_val: None` is also accepted to opt out of inspection)"
            )
            ret = exit_info.return_type
            if ret != BOOL and not is_void_like_type(ret):
                raise SemanticError(
                    f"__exit__ must return bool or None, got {ret}{exit_hint}",
                    exit_loc,
                )
            if len(exit_info.params) == 3:
                # exc_type (idx 0) and exc_tb (idx 2): must be None.
                for slot_name, slot_idx in (("exc_type", 0), ("exc_tb", 2)):
                    pt = exit_info.params[slot_idx].type
                    if not is_void_like_type(pt):
                        raise SemanticError(
                            f"__exit__ {slot_name} must be None, got "
                            f"{pt}{exit_hint}",
                            exit_loc,
                        )
                # exc_val (idx 1): None (no inspection) or
                # Optional[BaseException] (the builtin). Use registry
                # lookup rather than name compare so a user class named
                # `BaseException` (different qname / different record)
                # doesn't satisfy this.
                exc_val_t = exit_info.params[1].type
                base_exc_record = self.ctx.registry.find_record_by_qname(
                    qnames.BASE_EXCEPTION)
                ok = exc_val_t == NONE
                if (not ok and isinstance(exc_val_t, OptionalType)
                        and isinstance(exc_val_t.inner, NominalType)
                        and base_exc_record is not None):
                    inner_record = self.ctx.registry.find_record(
                        exc_val_t.inner.name)
                    ok = inner_record is base_exc_record
                if not ok:
                    raise SemanticError(
                        f"__exit__ exc_val must be None or "
                        f"Optional[BaseException], got {exc_val_t}{exit_hint}",
                        exit_loc,
                    )

        # Validate __getattr__ signature (D16 / dynamic attributes Phase 1).
        # Full rejection rules (decorators, async, generators, etc.) come in
        # Phase 4; Phase 1 enforces the load-bearing shape: param count, name
        # type, and return-type allow-list.
        if "__getattr__" in methods:
            ga_loc = _validate_dyn_dunder_kind(record, "__getattr__")
            ga_info = methods["__getattr__"][0]
            if len(methods["__getattr__"]) > 1:
                raise SemanticError(
                    "__getattr__ cannot be overloaded",
                    ga_loc,
                )
            if len(ga_info.params) != 1:
                raise SemanticError(
                    f"__getattr__ must take exactly one parameter besides self (the attribute name)",
                    ga_loc,
                )
            name_param = ga_info.params[0]
            if not is_str_type(name_param.type):
                raise SemanticError(
                    f"__getattr__ name parameter must be 'str', got {name_param.type}",
                    ga_loc,
                )
            ret = ga_info.return_type
            if ret is None or not _is_valid_dyn_getattr_return(ret):
                raise SemanticError(
                    f"__getattr__ return type must be a value type, Any, or Own[T]; "
                    f"got {ret} (bare reference / view types are not allowed)",
                    ga_loc,
                )

        # Validate __setattr__ signature (D16 Phase 2). Same baseline shape
        # as __getattr__: exact param count, str name, no overloads. Value
        # type V is unrestricted (any TPy type). Return must be None.
        if "__setattr__" in methods:
            sa_loc = _validate_dyn_dunder_kind(record, "__setattr__")
            sa_info = methods["__setattr__"][0]
            if len(methods["__setattr__"]) > 1:
                raise SemanticError(
                    "__setattr__ cannot be overloaded",
                    sa_loc,
                )
            if len(sa_info.params) != 2:
                raise SemanticError(
                    "__setattr__ must take exactly two parameters besides self "
                    "(the attribute name and the value)",
                    sa_loc,
                )
            sa_name_param = sa_info.params[0]
            if not is_str_type(sa_name_param.type):
                raise SemanticError(
                    f"__setattr__ name parameter must be 'str', got {sa_name_param.type}",
                    sa_loc,
                )
            sa_ret = sa_info.return_type
            if not isinstance(sa_ret, VoidType):
                raise SemanticError(
                    f"__setattr__ must return None, got {sa_ret}",
                    sa_loc,
                )

        # Validate __delattr__ signature (D16 Phase 3). Mirrors __setattr__
        # without the value param.
        if "__delattr__" in methods:
            da_loc = _validate_dyn_dunder_kind(record, "__delattr__")
            da_info = methods["__delattr__"][0]
            if len(methods["__delattr__"]) > 1:
                raise SemanticError(
                    "__delattr__ cannot be overloaded",
                    da_loc,
                )
            if len(da_info.params) != 1:
                raise SemanticError(
                    "__delattr__ must take exactly one parameter besides self "
                    "(the attribute name)",
                    da_loc,
                )
            da_name_param = da_info.params[0]
            if not is_str_type(da_name_param.type):
                raise SemanticError(
                    f"__delattr__ name parameter must be 'str', got {da_name_param.type}",
                    da_loc,
                )
            da_ret = da_info.return_type
            if not isinstance(da_ret, VoidType):
                raise SemanticError(
                    f"__delattr__ must return None, got {da_ret}",
                    da_loc,
                )

        # Build property registry from @property getter/setter methods
        properties: dict[str, PropertyInfo] = {}
        field_names = {f.name for f in record.fields}
        for method_name, overloads in list(methods.items()):
            for fi in overloads:
                if fi.is_property_getter:
                    if method_name in field_names:
                        raise SemanticError(
                            f"Property '{method_name}' conflicts with field of the same name",
                            record.loc,
                        )
                    # Use the first overload for PropertyInfo (for type inference).
                    # Both const and mutable overloads exist from parser cloning.
                    if method_name not in properties:
                        properties[method_name] = PropertyInfo(name=method_name, getter=fi)
                elif fi.is_property_setter:
                    setter_target = fi.property_name
                    if setter_target and setter_target in properties:
                        if properties[setter_target].setter is not None:
                            raise SemanticError(
                                f"Property '{setter_target}' already has a setter defined",
                                record.loc,
                            )
                        properties[setter_target].setter = fi
                    else:
                        raise SemanticError(
                            f"@property setter '{method_name}' has no matching @property getter",
                            record.loc,
                        )
        # Validate setter name doesn't conflict with existing methods
        for prop_name, prop_info in properties.items():
            if prop_info.setter is not None:
                setter_cpp_name = f"set_{prop_name}"
                if setter_cpp_name in methods:
                    raise SemanticError(
                        f"Property setter 'set_{prop_name}' conflicts with method '{setter_cpp_name}'",
                        record.loc,
                    )
        # Remove property methods from methods dict (not callable as
        # obj.method()). Pruning the redundant mutable getter clone for
        # value-typed returns happens later, in prune_value_property_clones:
        # a user record's is_value_type flag is only set during the protocol
        # pass, so checking it here misclassifies value-record returns and
        # leaves both clones alive (identical const C++ signatures ->
        # redefinition error).
        for prop_name in properties:
            methods.pop(prop_name, None)

        # Set provisional parent from bases (for get_all_fields during macro execution).
        # Full validation (protocols, circular check) is deferred to validate_record_inheritance.
        provisional_parent = None
        for base in record.bases:
            if isinstance(base, NominalType) and self.ctx.registry.get_record(base.name) is not None:
                provisional_parent = base
                break

        info = RecordInfo(
            name=record.name,
            fields=record.fields,
            has_init=record.init_method is not None or record.is_typed_dict,
            init_params=init_params,
            methods=methods,
            properties=properties,
            class_constants=class_constants,
            class_constants_finality=class_constants_finality,
            type_params=record.type_params,
            type_param_kinds=record.type_param_kinds,
            type_param_bounds=record.type_param_bounds,
            parents=[provisional_parent] if provisional_parent is not None else [],
            implemented_protocols=[],
            # @native records always carry a C++ name (defaulting to the
            # class's Python name when the user didn't provide a rename via
            # `@native("Foo")`). Forcing the leading `::` via ensure_qualified
            # keeps every reference unambiguously global, even when generated
            # code lives inside `namespace tpyapp::<module>`. Nested @native
            # records (parser-allowed but unused in the corpus) keep
            # native_name=None and fall through to the dotted-name registration
            # path in codegen_cpp/generator.py.
            native_name=(
                ensure_qualified(record.native_name) if record.native_name
                else (ensure_qualified(record.name)
                      if is_native and "." not in record.name else None)
            ),
            is_native=is_native,
            is_native_c=is_native_c,
            is_indirecting=record.is_indirecting,
            is_nocopy=record.is_nocopy,
            send_override=record.send_override,
            sync_override=record.sync_override,
            send_override_when=record.send_override_when,
            sync_override_when=record.sync_override_when,
            move_override=record.move_override,
            match_args=(
                record._macro_cls_info.get_match_args()
                if hasattr(record, '_macro_cls_info') and record._macro_cls_info is not None
                   and record._macro_cls_info.get_match_args() is not None
                else None
            ),
            is_frozen=record.is_frozen,
            is_typed_dict=record.is_typed_dict,
            is_total_false=record.is_total_false,
            has_del=record.del_method is not None,
            has_copy=has_copy,
            builtin_type_key=record.builtin_type_key,
            virtual_raise=record.virtual_raise,
            module=public_module_name(self.ctx.module_name, self.ctx.module_cpp_namespace) or None,
            defining_module=self.ctx.module_name,
            exposed_to_host=record.exposed_to_host,
            enum_companion_of=record.enum_companion_of,
        )
        # Adopt the pre-populated skeleton when available so peer
        # registries that captured a reference during bind_imports
        # see the freshly-finalized fields without a separate resync.
        decl_exports = self.ctx.module_decl_exports
        if decl_exports is not None and not record.builtin_type_key:
            existing_skeleton = decl_exports.records.get(record.name)
            if existing_skeleton is not None:
                info = _adopt_skeleton(existing_skeleton, info)
        self.ctx.registry.register_record(info)
        if record.enum_companion_of is not None:
            # Reached only through its enum: no name binding and no module
            # attribute, so Python code cannot spell it, and an importer of
            # the enum reaches it through the shared EnumInfo.
            td = type_def_of(receiver_self_type(record, self.ctx.registry))
            assert td is not None and td.enum is not None
            td.enum = dc_replace(td.enum, companion=info)
        else:
            self.ctx.global_ns.bind_record(info)
        # Per-module attribute table (Phase 1). Local definition: no
        # defining_module override, so binding is "owned by this module".
        # Skips builtin records (int32, list, ...) -- those are
        # attached to TypeDef and don't surface as module attributes.
        if not record.builtin_type_key and record.enum_companion_of is None:
            install_binding(
                self.ctx.module_attributes, record.name,
                SymbolKind.RECORD, info,
            )
        # Attach RecordInfo to the TypeDef registry under a stable qname:
        # - @builtin_type stubs (list, dict, Array, ...) attach onto the
        #   pre-existing static TypeDef by its builtin_type_key; the static
        #   entry keeps its category (LIST/ARRAY/...) and picks up the
        #   stub-contributed methods/fields.
        # - User records use `{module}.{name}`; entry-point records fall
        #   back to `__main__.{name}`. Mirrors the enum treatment so every
        #   record has a queryable TypeDef entry.
        # @builtin_type-with-body records (Task, future siblings) with no
        # pre-existing static TypeDef rely on the auto-derived is_send /
        # is_sync below: a field-walk diagnoses non-Send fields (e.g. Rc).
        # If a refactor strips such a field, is_send/is_sync silently flip
        # to True. Watch for that on field-shape changes to these records.
        if info.builtin_type_key:
            attach_dynamic_type_def(
                info.builtin_type_key,
                TypeCategory.RECORD,
                record=info,
            )
        else:
            record_td = attach_dynamic_type_def(
                info.qualified_name(),
                TypeCategory.RECORD,
                record=info,
            )
            # An @export class crosses the CPython boundary as its own
            # PyType_FromSpec type; mark its TypeDef so is_boundary_marshallable
            # admits it through the same per-type fact the scalars use.
            if info.exposed_to_host:
                record_td.boundary_marshal = True
        # Local class definition shadows any `from X import name` import:
        # if registry.functions still holds an entry under this name
        # (left over from an earlier function-import), clear it so the
        # class binding wins lookups. The attribute-table cell was
        # re-bound to RECORD by `install_binding` above.
        self.ctx.registry.functions.pop(info.name, None)

        # Warn when a field or method name shadows an auto-synthesized C++ method
        SYNTHESIZED_FROM_DUNDER = {
            "size": "__len__",
            "begin": "__span__",
            "end": "__span__",
        }
        field_names = {f.name for f in record.fields}
        method_names = {m.name for m in record.methods}
        user_names = field_names | method_names
        for synth_name, dunder in SYNTHESIZED_FROM_DUNDER.items():
            if synth_name in user_names and dunder in methods:
                self.ctx.warning_from_loc(
                    f"'{synth_name}' shadows auto-generated C++ {synth_name}() "
                    f"from {dunder}; consider renaming",
                    record.loc,
                )

        # C++ lookup cannot disambiguate a member and a nested type sharing
        # a name inside the same struct: `Outer::Foo::A` would resolve to
        # the member, not the enum, and `EnumUtil<Outer::Foo>` fails to
        # compile. No local rename rescues this; reject the collision.
        # Nested names are the dotted form (set by Parser._prefix_nested_names),
        # so strip the prefix for the short-name comparison.
        nested_type_names = {
            bare_name(nr.name) for nr in record.nested_records
        }
        nested_type_names.update(
            bare_name(ne.name) for ne in record.nested_enums
        )
        collisions = nested_type_names & user_names
        if collisions:
            collide = sorted(collisions)[0]
            raise SemanticError(
                f"Class '{record.name}' declares both a nested type '{collide}' "
                f"and a field/method '{collide}'; the generated C++ would be "
                f"ambiguous -- rename one of them",
                record.loc,
            )

    def validate_record_inheritance(self, record: TpyRecord) -> None:
        """Validate inheritance relationships for a record.

        Called after all records AND protocols are registered to allow forward references.
        This is where we classify bases into parent class vs protocol implementations.

        Supports inheritance from:
        - User-defined classes (NominalType with is_record)
        - Builtin types (ArrayType, etc.)
        - Protocols (NominalType with is_protocol)
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None:
            return

        # Native records can only inherit from other native records or protocols.
        # (the C++ inheritance already exists, we just track it for type checking)
        if record_info.is_native and record.bases:
            for base in record.bases:
                if isinstance(base, NominalType):
                    # Protocols are fine (express interface conformance, not C++ inheritance)
                    if self.ctx.registry.scan_by_short_name(base.name):
                        continue
                    base_record = self.ctx.registry.get_record(base.name)
                    if not base_record or not base_record.is_native:
                        raise SemanticError(
                            f"@{record.linkage.value} class '{record.name}' can only inherit from other @native classes",
                            record.loc
                        )

        # Classify bases into class-parents vs protocol implementations.
        # We do this here (not in register_record) so forward-referenced protocols are recognized.
        parents_collected: list[TpyType] = []
        implemented_protocols: list[NominalType] = []

        for base_type in record.bases:
            # Get the base name to check if it's actually a protocol
            base_name = None
            if isinstance(base_type, NominalType):
                base_name = base_type.name

            # Check if this base is actually a protocol (handles forward references)
            is_protocol_base = False
            if base_name and self.ctx.registry.scan_by_short_name(base_name) is not None:
                is_protocol_base = True

            if is_protocol_base:
                # It's a protocol implementation - set the is_protocol flag correctly
                protocol_type = base_type.with_protocol_flag(True) if isinstance(base_type, NominalType) else base_type
                # Set _module_qname from the protocol's registry entry so that
                # qualified_name() returns the correct module (not just the
                # _protocol_modules fallback which is first-write-wins)
                if isinstance(protocol_type, NominalType) and not protocol_type._module_qname:
                    proto_info = self.ctx.registry.scan_by_short_name(base_name)
                    if proto_info and proto_info.module:
                        # Use public module name for qualified_name() comparisons
                        pub_module = public_module_name(proto_info.module)
                        protocol_type = NominalType(
                            protocol_type.name, protocol_type.type_args, True,
                            f"{pub_module}.{protocol_type.name}",
                            protocol_type.is_dynamic_protocol)
                implemented_protocols.append(protocol_type)
            elif isinstance(base_type, NominalType) and base_type.is_record:
                # Check if parent is generic and requires type args
                parent_info = self.ctx.registry.get_record_for_type(base_type)
                if parent_info is None:
                    raise SemanticError(
                        f"Parent class '{base_type.name}' not defined for '{record.name}'",
                        record.loc
                    )
                if parent_info.is_generic() and not base_type.type_args:
                    raise SemanticError(
                        f"Generic class '{base_type.name}' requires type arguments in '{record.name}'. "
                        f"Use '{base_type.name}[T]' with appropriate type arguments.",
                        record.loc
                    )
                # Mint a qname-bearing parent so downstream `is_user_record`
                # (TypeDef-backed) resolves correctly on the stored reference.
                parent = base_type
                if (not base_type._module_qname
                        and not parent_info.builtin_type_key):
                    parent = NominalType(base_type.name, base_type.type_args,
                                         base_type.is_protocol,
                                         parent_info.qualified_name(),
                                         base_type.is_dynamic_protocol)
                parents_collected.append(parent)
            elif self._is_inheritable_builtin(base_type):
                parents_collected.append(base_type)
            else:
                raise SemanticError(
                    f"Invalid base type '{base_type}' in '{record.name}'. "
                    f"Only classes, builtin types, and protocols can be inherited.",
                    record.loc
                )

        # Update RecordInfo with classified bases.
        record_info.parents = parents_collected
        record_info.implemented_protocols = implemented_protocols
        # Invalidate the lazy `_is_polymorphic_class` cache: it may have
        # been computed during early validation (register_record's
        # validate_type call) when `implemented_protocols` was empty,
        # cementing a False answer for what's actually a polymorphic
        # class. Now that the protocol list is final, force recomputation
        # on next access.
        record_info._is_polymorphic_class = None

        # Direct C++ inheritance of a generic @dynamic protocol whose methods
        # have TypeParamRef in parameter position would emit overrides spelled
        # with the concrete type where the base virtual is spelled
        # `::tpy::param_val_or_ref_t<T>`, so the override does not bind the
        # virtual slot and the class stays abstract. A SIGNATURE mismatch, so
        # no call-site materialization reaches it. Reject with a clean
        # diagnostic until the override codegen spells the trait; the
        # structural-conformance (adapter) path already does.
        for proto in implemented_protocols:
            if not isinstance(proto, NominalType) or not proto.is_dynamic_protocol:
                continue
            proto_info = self.ctx.registry.scan_by_short_name(proto.name)
            if proto_info is None or not proto_info.type_params:
                continue
            for method_sig in proto_info.methods:
                for pname, ptype in method_sig.params:
                    if contains_type_param(ptype):
                        raise SemanticError(
                            f"Class '{record.name}' cannot directly inherit generic "
                            f"@dynamic protocol '{proto}': method '{method_sig.name}' "
                            f"has type parameter '{ptype}' in parameter position, which "
                            f"the direct-inheritance codegen does not yet support. Use "
                            f"structural conformance instead (remove the explicit base; "
                            f"the adapter path handles parameterized params correctly).",
                            record.loc,
                        )

        # Reject circular inheritance across any ancestor chain before C3 runs
        # (C3 would fail as "inconsistent ordering" but the message would be less targeted).
        for p in record_info.parents:
            if isinstance(p, NominalType) and p.is_user_record:
                if self._has_circular_inheritance(record.name, p.name):
                    raise SemanticError(
                        f"Circular inheritance detected: '{record.name}' inherits from '{p.name}'",
                        record.loc
                    )

        # Reject diamond inheritance with a targeted diagnostic before running C3.
        # Under v1's single-parent gate this is a no-op (detect_diamond early-returns
        # for len(parents) < 2); D22's gate flip automatically activates it.
        diamond = self.ctx.registry.detect_diamond(record_info)
        if diamond is not None:
            anc_name, p1_name, p2_name = diamond
            raise SemanticError(
                f"Diamond inheritance not supported: '{anc_name}' is reachable from "
                f"multiple bases of '{record.name}' (via '{p1_name}' and '{p2_name}'). "
                f"Use @dynamic for runtime polymorphism.",
                record.loc,
            )

        # Compute C3 linearization. Under v1's single-parent invariant this is trivially
        # [parent] + parent.mro_ancestors, but the algorithm runs as-is so D22's multi-base
        # path works without further plumbing. Circular-inheritance was already rejected
        # above, so compute_mro_ancestors cannot recurse infinitely here.
        try:
            record_info.mro_ancestors = self.ctx.registry.compute_mro_ancestors(record_info)
        except C3LinearizationError as exc:
            raise SemanticError(
                f"Inconsistent base-class ordering in '{record.name}': {exc}",
                record.loc,
            ) from exc

        # Phase 20 Stage 2c: Throwable conformance rules.
        # (1) Concrete Throwable implementers must inherit builtins.BaseException
        #     (BaseException itself is the only direct Throwable implementer; it
        #     provides the `message` field that codegen's auto-emitted `what()`
        #     reads). (2) Throwable implementers must be copy-constructible at
        #     the C++ level (auto-emitted `clone()` / `__raise__()` do
        #     `make_unique<This>(*this)` / `throw *this`). Both checks are
        #     qname-keyed so user code with a locally-named `Throwable` /
        #     `BaseException` does not collide with the compiler-known rule.
        self._check_throwable_conformance(record, record_info)

        # Mirror Python's MRO-based ctor inheritance: 'class B(A): pass' should
        # accept whatever A() does; mixins (`class C(Base, Mixin): pass`) work
        # the same way when Mixin contributes no __init__.
        # Two+ init-bearing parents stay rejected (silently picking MRO-first
        # is more error-prone than helpful). Generic parents are skipped: their
        # init_params reference unsubstituted type params. @native records are
        # excluded because their C++ struct may not inherit the parent's ctors
        # (e.g. tpy::StopIteration uses `{}`).
        init_parent = self._find_unique_init_parent(record_info)
        if init_parent is not None:
            parent_info = self.ctx.registry.get_record_for_type(init_parent)
            record_info.has_init = True
            record_info.inherits_init_from = init_parent
            # A generic-base instantiation (`Base[14]`) carries the type args,
            # so substitute its params into the inherited init params (the
            # substitution is empty for a non-generic base -- a verbatim copy).
            subst = self.type_ops.build_type_substitution(init_parent)
            record_info.init_params = [
                (name, self.type_ops.substitute_type_params(ptype, subst), default)
                for (name, ptype, default) in parent_info.init_params
            ]

        # Auto-declare fields from `self.f = param` in this record's OWN
        # __init__. Base-less classes are handled at parse time; a class WITH
        # bases must wait until here, where the resolved MRO lets us skip names
        # already declared by an ancestor -- assigning an inherited name writes
        # the parent's slot and must NOT be re-declared as a shadow field.
        # Non-default-linkage (@native) records keep their C++-owned fields.
        if record.bases and record.linkage == RecordLinkage.DEFAULT:
            init_method = record.init_method
            if init_method is not None:
                visible = self.ctx.registry.get_all_fields(record_info)
                inferred = auto_declare_fields_from_init(
                    init_method, visible, set(record_info.properties.keys()))
                if inferred:
                    # Inferred fields are added after register_record's field
                    # validation, so run the same per-field checks here (the
                    # base-less path validates at registration) -- otherwise a
                    # protocol/Self/Fn/redundant-Own inferred field slips
                    # through to ill-formed C++.
                    is_gen = bool(record.type_params)
                    for fld in inferred:
                        self._validate_instance_field(fld, record, is_gen)
                    # record_info.fields IS record.fields (same list), so mutate
                    # in place -- both the AST (codegen) and RecordInfo (sema)
                    # see it. Reorder to __init__ assignment order (matching the
                    # parser's base-less path) so the C++ struct-member and
                    # init-list orders agree -- else -Werror=reorder.
                    record.fields[:] = reorder_fields_by_init(
                        init_method, record.fields + inferred)

        # Needs MRO, so cannot run earlier.
        self._check_field_shadowing(record, record_info)
        self._check_return_exception_fields(record, record_info)

        if len(record_info.parents) >= 2:
            self._check_multi_base_order(record, record_info)
            self._check_multi_base_method_conflicts(record, record_info)
            # Same-name fields across ancestors are legal; ambiguity at
            # `self.x` fires from _try_find_field instead.
            # Every base with __init__ must be invoked explicitly from the
            # child's __init__. Deferred to validate_multi_base_init_calls so
            # the check can inspect the already-analyzed __init__ body.

        # Coordinate method hiding and @override checks.
        # @override methods are handled by _check_override_annotations (which emits a more
        # targeted non-polymorphic warning) and are skipped by _check_method_hiding.
        override_method_names = {m.name for m in record.methods if m.is_override}

        # Check for method hiding (child defines method with same name as parent)
        if record_info.parents:
            self._check_method_hiding(record, record_info, override_method_names)

        # Validate @override annotations (error if no match; warn if non-polymorphic)
        if override_method_names:
            self._check_override_annotations(record, record_info)

        # Validate protocol implementations
        for protocol in record_info.implemented_protocols:
            # Send/Sync opt-in markers are validated by the auto-derive
            # below (_check_marker_claim names the offending field); the
            # generic conformance check would consult is_send/is_sync
            # before they are derived.
            if protocol.qualified_name() in (qnames.SEND, qnames.SYNC):
                continue
            protocol_info = protocol_info_of(protocol)
            if protocol_info is None:
                raise SemanticError(
                    f"Protocol '{protocol.name}' not defined for implementation in '{record.name}'",
                    record.loc
                )

            # Check if record implements all protocol methods.
            # For @builtin_type classes, use the concrete type (e.g. FLOAT32)
            # so Self-substitution in protocol signatures matches method param types.
            record_type: TpyType = NominalType(
                record.name, _module_qname=record_info.qualified_name()
            )
            if record_info.builtin_type_key:
                record_type = builtin_modules.get_builtin_type_obj(record_info.builtin_type_key) or record_type
            if not self.protocols.type_conforms_to_protocol(record_type, protocol):
                # Generate helpful error message describing each conformance issue
                issues = self.protocols.get_protocol_conformance_issues(record_type, protocol)
                if issues:
                    detail = "; ".join(issues)
                    raise SemanticError(
                        f"Class '{record.name}' does not conform to protocol "
                        f"'{protocol}': {detail}",
                        record.loc
                    )
                # Fallback when the detailed scan didn't surface a reason
                # (e.g. marker-protocol / extends gaps checked elsewhere).
                raise SemanticError(
                    f"Class '{record.name}' declares implementation of protocol '{protocol}' "
                    f"but does not satisfy the protocol requirements",
                    record.loc
                )

        # NativeIterable's C++ concept requires `std::ranges::begin(t)`/`end(t)`,
        # which TPy can only guarantee for built-ins backed by hand-written C++
        # ranges. Use Spannable[T] for span-backed types or Iterable[T] otherwise.
        if not record_info.is_native:
            for protocol in record_info.implemented_protocols:
                if protocol.qualified_name() == qnames.NATIVE_ITERABLE:
                    raise SemanticError(
                        f"Class '{record.name}' cannot implement NativeIterable: "
                        f"NativeIterable is reserved for @native types. "
                        f"Use Spannable[T] for span-backed types, or Iterable[T] "
                        f"for general iteration.",
                        record.loc
                    )

        # ValueType marker: set flag (field validation deferred to a second pass
        # so that all ValueType records in the module are registered first)
        for protocol in record_info.implemented_protocols:
            if protocol.qualified_name() == "tpy.ValueType":
                if record_info.is_nocopy:
                    raise SemanticError(
                        f"@nocopy class '{record.name}' cannot implement ValueType "
                        f"(value types require copy semantics)",
                        record.loc
                    )
                record_info.is_value_type = True
                break

        # ReturnException marker: register exception type as return-only
        if return_exception_marker(record_info) is not None:
            register_return_exception(record.name)

        # Auto-derive Send/Sync based on field types.
        # A record is Send if all its fields are Send (safe to move across threads).
        # A record is Sync if all its fields are Sync (safe to share across threads).
        # Fields whose type mentions an unresolved type parameter are
        # assumed OK at registration -- enforced at C++ instantiation
        # via concepts, and re-checked under concrete type_args by
        # NominalType.is_send / is_sync at every use site.
        # NOTE: Modules are compiled in dependency order, so parent records from
        # imported modules are already attached to their TypeDef.record payload.
        is_send = all(
            f.type.is_send() or contains_type_param(f.type)
            for f in record_info.fields
        )
        is_sync = all(
            f.type.is_sync() or contains_type_param(f.type)
            for f in record_info.fields
        )
        for p in record_info.parents:
            is_send = is_send and (p.is_send() or contains_type_param(p))
            is_sync = is_sync and (p.is_sync() or contains_type_param(p))

        # Opt-in markers (`class Foo(Send)`) assert the structural answer is
        # true and lock the contract: silently adding a non-Send field later
        # errors instead of quietly flipping the derived answer. Decorator
        # overrides on the same trait are mutually exclusive with the marker
        # (@unsafe_* is redundant with it; @nosend/@nosync contradicts it).
        for protocol in record_info.implemented_protocols:
            qn = protocol.qualified_name()
            if qn == qnames.SEND:
                self._check_marker_claim(
                    record, record_info, "Send", is_send,
                    record_info.send_override, record_info.send_override_when,
                    lambda t: t.is_send() or contains_type_param(t))
            elif qn == qnames.SYNC:
                self._check_marker_claim(
                    record, record_info, "Sync", is_sync,
                    record_info.sync_override, record_info.sync_override_when,
                    lambda t: t.is_sync() or contains_type_param(t))

        # Decorator overrides force the answer regardless of fields.
        if record_info.send_override is not None:
            is_send = record_info.send_override
        if record_info.sync_override is not None:
            is_sync = record_info.sync_override
        record_info.is_send = is_send
        record_info.is_sync = is_sync

        # Movability: a record is movable iff every field and parent is, OR it
        # supplies a relocating move via __move__ (the escape, mirroring how
        # __copy__ escapes nocopy). @nomove forces non-movable. Generic records
        # re-walk per use-site in NominalType.is_movable; here we fold the
        # non-generic answer (and the override/has_move escapes for both).
        is_movable = all(
            f.type.is_movable() or contains_type_kind_param(f.type)
            for f in record_info.fields
        )
        for p in record_info.parents:
            is_movable = is_movable and (p.is_movable() or contains_type_kind_param(p))
        record_info.has_move = record.move_method is not None
        if record_info.has_move:
            is_movable = True
        if record_info.move_override is not None:
            is_movable = record_info.move_override
        record_info.is_movable = is_movable

        # Record-side closure for type_def_registry.is_subtype. Scope is
        # DIRECT implemented protocols + their parent chains; protocols
        # inherited via MRO ancestor records are intentionally NOT
        # included (codegen's is_polymorphic_class_type walks MRO
        # separately for the bases-have-vtable question).
        supertypes: set[str] = set()
        for proto in record_info.implemented_protocols:
            supertypes.add(proto.name)
            proto_info = protocol_info_of(proto)
            if proto_info is not None:
                supertypes.update(proto_info.transitive_supertypes)
        record_info.transitive_supertypes = frozenset(supertypes)

    def _check_marker_claim(
        self, record: TpyRecord, record_info: 'RecordInfo', trait: str,
        structural_ok: bool, override: 'bool | None',
        override_when: 'tuple[str, ...] | None', field_ok,
    ) -> None:
        """Validate a `class Foo(Send)` / `class Foo(Sync)` opt-in marker:
        the structural derivation must agree (the claim is checked, not
        trusted), and trait-override decorators on the same record are
        rejected (redundant for @unsafe_*, contradictory for @no*, conflicting
        for a conditional @unsafe_* with if_params_* kwargs)."""
        unsafe_dec = f"@unsafe_{trait.lower()}"
        opt_out_dec = f"@no{trait.lower()}"
        if override is not None:
            relation = "is redundant with" if override else "contradicts"
            dec = unsafe_dec if override else opt_out_dec
            raise SemanticError(
                f"{dec} {relation} the '{trait}' base class on "
                f"'{record.name}' -- use one or the other",
                record.loc)
        # The marker base asserts an unconditional structural claim; the
        # conditional override asserts a per-type-param one. They conflict.
        if override_when is not None:
            raise SemanticError(
                f"a conditional @unsafe_{trait.lower()}(if_params_...) conflicts "
                f"with the '{trait}' base class on '{record.name}' -- use one or "
                f"the other", record.loc)
        if not structural_ok:
            offending = next(
                (f for f in record_info.fields if not field_ok(f.type)), None)
            if offending is not None:
                detail = f"field '{offending.name}: {offending.type}'"
                offending_type = offending.type
            else:
                parent = next(
                    (p for p in record_info.parents if not field_ok(p)), None)
                detail = f"base class '{parent}'"
                offending_type = parent
            send = trait == "Send"
            chain = (why_not_send(offending_type) if send
                     else why_not_sync(offending_type)) if offending_type is not None else None
            chain_detail = f"\n{render_chain(chain, send)}" if chain is not None else ""
            raise SemanticError(
                f"Class '{record.name}' declares {trait} but {detail} is "
                f"not {trait}{chain_detail}",
                record.loc)

    def _check_throwable_conformance(self, record: TpyRecord, record_info: 'RecordInfo') -> None:
        """Materialize Throwable-implementer facts on RecordInfo and apply
        the Phase 20 sema rules. Sets `implements_throwable` /
        `inherits_base_exception` so codegen (auto-emit) and downstream
        sema sites read one source of truth instead of re-walking the MRO.
        """
        # Single pass over [self, *ancestors] collecting both qname matches.
        implements_throwable = False
        inherits_base_exception = False
        for info in [record_info, *self.ctx.registry.iter_ancestor_records(record_info)]:
            for proto in info.implemented_protocols:
                if isinstance(proto, NominalType) and proto.qualified_name() == qnames.THROWABLE:
                    implements_throwable = True
                    break
            if info is not record_info and info.qualified_name() == qnames.BASE_EXCEPTION:
                inherits_base_exception = True
            if implements_throwable and inherits_base_exception:
                break
        is_return_exception = return_exception_marker(record_info) is not None
        record_info.is_return_exception = is_return_exception
        record_info.inherits_base_exception = inherits_base_exception
        self._check_no_return_exception_parent(record, record_info)
        if is_return_exception:
            self._check_return_exception_base(record, record_info)
            implements_throwable = False
        record_info.implements_throwable = implements_throwable
        if not implements_throwable:
            return
        qname = record_info.qualified_name()
        # BaseException itself is the unique root direct implementer.
        if qname != qnames.BASE_EXCEPTION and not inherits_base_exception:
            raise SemanticError(
                f"Exception class '{record.name}' implements Throwable but does "
                f"not inherit BaseException; use 'class {record.name}(Exception)' "
                f"or another BaseException subclass as the base. Throwable is "
                f"the ABI protocol; concrete exception classes extend through "
                f"BaseException, which provides `message` and the standard "
                f"`__str__` / `what()` shape.",
                record.loc,
            )
        # Auto-emitted clone() / __raise__() need a usable copy ctor.
        record_type = NominalType(
            record.name, (), False, qname, False,
        )
        if self.ctx.is_type_non_copyable(record_type):
            raise SemanticError(
                f"Exception class '{record.name}' is not copy-constructible; "
                f"classes implementing Throwable must be copy-constructible "
                f"(required by auto-emitted `clone()` and `__raise__()`). "
                f"Remove any @nocopy / __del__ on the class or its fields, or "
                f"do not inherit from BaseException.",
                record.loc,
            )
        # User overrides of the auto-emitted ABI methods would silently
        # produce a C++ redefinition; @native classes are exempt (codegen
        # skips their structs entirely, the runtime macro provides the
        # overrides).
        if not record_info.is_native:
            for method in record.methods:
                if method.name in qnames.THROWABLE_ABI_METHODS:
                    raise SemanticError(
                        f"Throwable subclass '{record.name}' cannot define "
                        f"'{method.name}': the Throwable ABI methods "
                        f"(clone/__raise__/what) are codegen-emitted "
                        f"automatically and a user override would collide. "
                        f"Remove this method to let the auto-emit provide it.",
                        method.loc or record.loc,
                    )

    def _check_no_return_exception_parent(self, record: TpyRecord,
                                          record_info: 'RecordInfo') -> None:
        """A return exception is handled by EXACT type -- `except Base` never
        takes a `Sub` -- so a subclass would add no dispatch, only a second
        struct to keep in step; and one without the marker would be a thrown
        exception over a base that is not Throwable. Read the parent's marker
        off its protocols, not its flag: a parent declared later in the module
        has not been through this pass yet."""
        for parent in record_info.parents:
            parent_info = self.ctx.registry.get_record_for_type(parent)
            if parent_info is None:
                continue
            if return_exception_marker(parent_info) is not None:
                raise SemanticError(
                    f"'{record.name}' cannot subclass '{parent_info.name}': a "
                    f"return-only exception (ReturnException) is handled by "
                    f"exact type and cannot be subclassed; declare "
                    f"'class {record.name}(Exception, ReturnException)' on its "
                    f"own",
                    record.loc)

    def _check_return_exception_fields(self, record: TpyRecord,
                                       record_info: 'RecordInfo') -> None:
        """A field that re-declares one of the thrown ancestors' fields keeps
        its type: `str(e)` is rendered from `message`, and a differently typed
        one would silently print nothing where CPython prints the value."""
        if not record_info.is_return_exception or record_info.is_native:
            return
        for anc in self.ctx.registry.iter_ancestor_records(record_info):
            if self.ctx.registry.is_struct_base(record_info, anc):
                continue
            for inherited in anc.fields:
                for fld in record.fields:
                    if fld.name == inherited.name and fld.type != inherited.type:
                        raise SemanticError(
                            f"Field '{fld.name}' of return-only exception "
                            f"'{record.name}' must be '{inherited.type}' "
                            f"(str() reads it); got '{fld.type}'",
                            fld.loc or record.loc)

    def _check_return_exception_base(self, record: TpyRecord,
                                     record_info: 'RecordInfo') -> None:
        """A return exception's C++ struct swaps its one `Exception` base for
        the non-throwable value base, so any other class parent -- a concrete
        exception like `ValueError`, or a user exception class -- would drag
        the Throwable hierarchy back in."""
        if record_info.is_native:
            return
        parents = [p.qualified_name() for p in record_info.parents
                   if isinstance(p, NominalType)]
        if parents != [qnames.EXCEPTION]:
            raise SemanticError(
                f"ReturnException class '{record.name}' must derive directly "
                f"from Exception: 'class {record.name}(Exception, "
                f"ReturnException)'",
                record.loc)

    def validate_method_error_returns(self, record: TpyRecord) -> None:
        """Validate @error_return(E) on methods references a ReturnException type.

        Deferred from register_record because ReturnException markers are set
        during validate_record_inheritance, which runs after register_record.
        """
        for method in record.methods:
            if method.error_return and not is_return_exception(method.error_return):
                raise SemanticError(
                    f"'{method.error_return}' is not a ReturnException type; "
                    f"@error_return requires a ReturnException exception",
                    method.loc
                )

    def validate_record_field_protocols(self, record: TpyRecord) -> None:
        """Re-validate record field + method-signature types now that all
        protocols and sibling records are registered.

        register_record validates fields before protocols register, so checks
        that depend on protocol_info_of (e.g. Optional[@dynamic protocol]
        rejection) and on sibling records' methods (Hashable/Equatable
        conformance for dict-key / set-element gating) silently pass. This
        second pass re-runs validate_type on each field and on every method
        param / return type so those checks fire correctly.
        """
        is_generic = bool(record.type_params)
        for fld in record.fields:
            if fld.type is None:
                continue
            self.type_ops.validate_type(fld.type, allow_type_param_ref=is_generic, loc=fld.loc)
        for method in record.methods:
            for _, ptype in method.params:
                if ptype is not None and not contains_type_param(ptype):
                    self.type_ops.validate_type(
                        ptype, allow_type_param_ref=is_generic,
                        loc=method.loc or record.loc,
                    )
            if (method.return_type is not None
                    and not contains_type_param(method.return_type)):
                self.type_ops.validate_type(
                    method.return_type, allow_type_param_ref=is_generic,
                    loc=method.loc or record.loc,
                )

    def prune_value_property_clones(self, record: TpyRecord) -> None:
        """Drop the mutable getter clone of a @property whose return is a
        value type -- a single const overload suffices (no T& vs const T&
        aliasing distinction), and keeping both emits two identical const
        C++ signatures (redefinition error).

        Runs with validate_value_type_fields, after the protocol pass, so
        user-record ValueType flags are authoritative (register_record is
        too early: a value record's flag is not yet set there).
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None or not record_info.properties:
            return
        for prop_name, prop_info in record_info.properties.items():
            ret = prop_info.getter.return_type
            if ret is not None and ret.is_value_type():
                record.methods = [
                    m for m in record.methods
                    if not (m.is_property_getter and m.name == prop_name
                            and m.is_auto_readonly_mutable_clone)
                ]

    def validate_value_type_fields(self, record: TpyRecord) -> None:
        """Validate that all fields of a ValueType record are themselves value types,
        and that any parent class is also a value type.

        Called in a second pass after all records have been processed, so that
        ValueType records defined later in the same module are already registered.
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None or not record_info.is_value_type:
            return
        for p in record_info.parents:
            if not p.is_value_type():
                raise SemanticError(
                    f"ValueType class '{record.name}': parent '{p}' is not a value type",
                    record.loc
                )
        for fld in record_info.fields:
            if not self._is_field_value_type(fld.type):
                raise SemanticError(
                    f"ValueType class '{record.name}': field '{fld.name}' "
                    f"has non-value type '{fld.type}'",
                    fld.loc
                )
        # Value types are immutable, so fields can only be set in __init__;
        # an explicit one is required (no synthesized aggregate ctor) to keep
        # construction CPython-portable. @native value types are constructed
        # on the C++ side and carry no TPy __init__. Checked after the
        # structural validations so a bad parent/field is reported first.
        # has_init covers own and inherited __init__.
        if not (record_info.has_init or record_info.is_native):
            raise SemanticError(
                f"ValueType class '{record.name}' must define an explicit __init__",
                record.loc
            )

    @staticmethod
    def _is_field_value_type(typ: TpyType) -> bool:
        """Check if a field type satisfies the ValueType constraint.

        Type parameters are accepted unconditionally -- for generic ValueType
        records, the C++ is_value_type trait enforces this at instantiation time.
        """
        if typ.is_value_type():
            return True
        if isinstance(typ, TypeParamRef):
            return True
        return False

    def _check_multi_base_order(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Verify that source base-declaration order matches C3 linearization order.

        C3's local precedence invariant normally guarantees this, so the check is
        defensive -- but it also enforces Q4-style "no silent reordering": if ever
        the two diverge (e.g. due to a transitional linearization bug), the user
        sees an error directing them to reorder, rather than codegen silently
        emitting a different C++ subobject construction order.
        """
        positions = [
            next(i for i, anc in enumerate(record_info.mro_ancestors) if same_base_type(p, anc))
            for p in record_info.parents
        ]
        for i in range(1, len(positions)):
            if positions[i] <= positions[i - 1]:
                src_names = [
                    p.name if isinstance(p, NominalType) else str(p)
                    for p in record_info.parents
                ]
                raise SemanticError(
                    f"Base-class order for '{record.name}' differs from C3 linearization: "
                    f"source order {src_names} is not MRO-compatible. Reorder bases to match MRO.",
                    record.loc
                )

    def _find_unique_init_parent(self, record_info: RecordInfo) -> NominalType | None:
        """Return the parent whose __init__ this record can inherit, or None.

        Returns None when the record itself has __init__/fields/is_native, when
        no parent has __init__, when more than one does, or when the candidate
        is generic (would leak unsubstituted type params).
        """
        if (record_info.has_init
                or record_info.fields
                or record_info.is_native
                or not record_info.parents):
            return None
        found: NominalType | None = None
        for parent in record_info.parents:
            if not isinstance(parent, NominalType):
                continue
            parent_info = self.ctx.registry.get_record_for_type(parent)
            if parent_info is None or not parent_info.has_init:
                continue
            # No `Exception(message)` to inherit on a return exception.
            if not self.ctx.registry.is_struct_base(record_info, parent_info):
                continue
            # A generic parent is inheritable only as a concrete instantiation
            # (`class Sub(Base[14])`): the type args then substitute the init
            # params to concrete types at the copy site. A bare/unbound generic
            # base would leak unsubstituted params, so it stays excluded.
            if parent_info.type_params and (
                    len(parent.type_args) != len(parent_info.type_params)):
                return None
            if found is not None:
                return None
            found = parent
        return found

    def _check_field_shadowing(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Warn when the record's own field shadows an inherited one."""
        for fld in record.fields:
            for anc_rec in self.ctx.registry.iter_field_ancestors(record_info):
                if any(af.name == fld.name for af in anc_rec.fields):
                    self.ctx.warning_from_loc(
                        f"Field '{fld.name}' in '{record.name}' shadows "
                        f"inherited field from '{anc_rec.name}'; use "
                        f"'{anc_rec.name}.{fld.name}' to access the ancestor's",
                        fld.loc or record.loc,
                    )
                    break

    def validate_multi_base_init_calls(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Every base with __init__ must be called explicitly from the child's
        __init__. BaseN.__init__(self, ...) covers each base by name;
        super().__init__(...) covers only the MRO-first base with __init__
        (v2.3: MRO-aware resolution), so other bases still need explicit
        BaseN.__init__ calls.

        Runs after __init__ body analysis so unbound-self calls have been
        resolved (expr.unbound_self_parent_type set by MethodAnalyzer).
        Scope: multi-base classes only. Single-base inheritance uses the
        existing super().__init__() path.
        """
        if len(record_info.parents) < 2:
            return
        # Only one initializing base means sister bases default-construct via
        # `using <Base>::<Base>;` -- no explicit per-base call needed.
        if record_info.inherits_init_from is not None:
            return
        bases_with_init: list[tuple[str, NominalType]] = []
        for p in record_info.parents:
            if not isinstance(p, NominalType):
                continue
            p_info = self.ctx.registry.get_record_for_type(p)
            if p_info is None:
                continue
            if self.ctx.registry.get_method_overloads_with_parents(p_info, "__init__"):
                bases_with_init.append((p_info.name, p))
        if not bases_with_init:
            return

        # Child must define __init__ to call them.
        if record.init_method is None:
            names = ", ".join(n for n, _ in bases_with_init)
            raise SemanticError(
                f"Multi-base class '{record.name}' inherits __init__ from bases ({names}); "
                f"define '{record.name}.__init__' and invoke each base's __init__ explicitly "
                f"(via BaseN.__init__(self, ...); super().__init__(...) covers the MRO-first base only).",
                record.loc,
            )

        def base_name_of(stmt: TpyStmt) -> str | None:
            if not isinstance(stmt, TpyExprStmt):
                return None
            expr = stmt.expr
            if not (isinstance(expr, TpyMethodCall) and expr.method == "__init__"):
                return None
            pt = expr.unbound_self_parent_type or expr.super_parent_type
            if pt is None:
                return None
            pt_info = self.ctx.registry.get_record_for_type(pt)
            return pt_info.name if pt_info is not None else None

        call_sequence: list[tuple[str, TpyExprStmt]] = []
        for stmt in record.init_method.body:
            n = base_name_of(stmt)
            if n is not None:
                assert isinstance(stmt, TpyExprStmt)
                call_sequence.append((n, stmt))
        called_base_names: set[str] = {n for n, _ in call_sequence}

        # Treat base inits nested in control flow as a distinct, more actionable
        # error so users aren't misled by a generic "missing calls" message when
        # they did write the call but placed it inside an if/loop/try branch.
        nested_base_names: set[str] = set()
        def walk_nested(stmts: list[TpyStmt]) -> None:
            for stmt in stmts:
                sub = getattr(stmt, "sub_bodies", None)
                if sub is None:
                    continue
                for body in sub():
                    for inner in body:
                        n = base_name_of(inner)
                        if n is not None and n not in called_base_names:
                            nested_base_names.add(n)
                    walk_nested(body)
        walk_nested(record.init_method.body)

        if nested_base_names:
            raise SemanticError(
                f"Base __init__ calls must be top-level statements in "
                f"'{record.name}.__init__', not nested in control flow; "
                f"found nested call(s) for: {', '.join(sorted(nested_base_names))}.",
                record.init_method.loc,
            )

        missing = [n for n, _ in bases_with_init if n not in called_base_names]
        if missing:
            raise SemanticError(
                f"Multi-base class '{record.name}' must call __init__ on every base that "
                f"defines one; missing calls for: {', '.join(missing)}. "
                f"Invoke each via 'BaseN.__init__(self, ...)'; 'super().__init__(...)' "
                f"covers the MRO-first base only.",
                record.init_method.loc,
            )

        # Warn when the source order of the calls disagrees with declaration
        # order. Codegen hoists them into the MIL in declaration order regardless
        # (C++ runs base ctors that way no matter how the list is written), so
        # the user's source order is misleading: argument evaluation order
        # shifts too.
        declared_rank: dict[str, int] = {n: i for i, (n, _) in enumerate(bases_with_init)}
        for i in range(1, len(call_sequence)):
            prev_name, _ = call_sequence[i - 1]
            curr_name, curr_stmt = call_sequence[i]
            if prev_name in declared_rank and curr_name in declared_rank:
                if declared_rank[curr_name] < declared_rank[prev_name]:
                    self.ctx.warning(
                        f"Base __init__ calls in '{record.name}.__init__' are written "
                        f"out of declaration order ('{curr_name}' after '{prev_name}', "
                        f"but '{curr_name}' is declared before '{prev_name}'). C++ runs "
                        f"base constructors -- and evaluates their argument expressions "
                        f"-- in declaration order regardless; rewrite the calls in "
                        f"declaration order to match runtime behavior.",
                        curr_stmt,
                    )
                    break

    def _check_multi_base_method_conflicts(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Error when two direct-parent chains provide the same method name and
        the child does not override it.

        For each direct parent p, compute the set of method names reachable via p
        (p's own methods plus everything visible up p's own MRO). Any method that
        appears in more than one parent's set must be overridden by the child.
        __init__/__del__ are exempt: they are never polymorphically inherited, and
        the child's body must explicitly call each base's constructor/destructor.
        """
        parent_method_sets: list[tuple[str, set[str]]] = []
        for p in record_info.parents:
            if not isinstance(p, NominalType):
                continue
            p_rec = self.ctx.registry.get_record_for_type(p)
            if p_rec is None:
                continue
            names: set[str] = set(p_rec.methods.keys())
            for anc_rec in self.ctx.registry.iter_ancestor_records(p_rec):
                names.update(anc_rec.methods.keys())
            parent_method_sets.append((p_rec.name, names))

        own_methods = set(record_info.methods.keys())
        for i in range(len(parent_method_sets)):
            for j in range(i + 1, len(parent_method_sets)):
                p1_name, p1_methods = parent_method_sets[i]
                p2_name, p2_methods = parent_method_sets[j]
                for m in sorted(p1_methods & p2_methods):
                    if m in ("__init__", "__del__"):
                        continue
                    if m in own_methods:
                        continue
                    raise SemanticError(
                        f"Method '{m}' defined by both '{p1_name}' and '{p2_name}' "
                        f"(bases of '{record.name}'); '{record.name}' must override to disambiguate.",
                        record.loc
                    )

    def _has_circular_inheritance(self, record_name: str, parent_name: str) -> bool:
        """Check if record_name is reachable from parent_name via any ancestor chain."""
        visited: set[str] = set()
        stack: list[str] = [parent_name]
        while stack:
            current = stack.pop()
            if current == record_name:
                return True
            if current in visited:
                continue
            visited.add(current)
            parent_info = self.ctx.registry.get_record(current)
            if parent_info is None:
                continue
            for p in parent_info.parents:
                if isinstance(p, NominalType):
                    stack.append(p.name)
        return False

    def _check_method_hiding(self, record: TpyRecord, record_info: RecordInfo,
                              skip_names: set[str] | None = None) -> None:
        """Warn when child class defines method with same name as parent.

        In Python, methods use dynamic dispatch (virtual by default).
        In C++, methods use static dispatch (non-virtual by default).
        This causes different behavior when a parent method calls self.method().

        skip_names: method names to skip (e.g. @override methods handled separately).
        """
        if not record_info.parents:
            return

        # Methods explicitly marked as hiding parent versions
        hides = {m.name for m in record.methods if m.hides_parent}

        # Check each method defined in this class
        for method_name in record_info.methods:
            if method_name in ("__init__", "__del__"):
                continue  # constructor/destructor hiding is expected
            if skip_names and method_name in skip_names:
                continue  # @override methods are checked (with better messages) separately
            if method_name in hides:
                continue

            # Check if any MRO ancestor has this method
            ancestor_with_method = self._find_mro_ancestor_with_method(record_info, method_name)
            if ancestor_with_method:
                # If this method is part of a @dynamic protocol implemented
                # (transitively) by any ancestor, codegen emits it as `override`
                # on a real virtual slot -- not a hide. Suppress the warning.
                if self._method_is_transitively_dynamic_protocol_member(
                        record_info, method_name):
                    continue
                self.ctx.warning(
                    f"Method '{record.name}.{method_name}' hides "
                    f"'{ancestor_with_method}.{method_name}' -- any "
                    f"'{ancestor_with_method}' reference will call "
                    f"'{ancestor_with_method}.{method_name}', not "
                    f"'{record.name}.{method_name}' (differs from Python's dynamic dispatch); "
                    f"make '{ancestor_with_method}' a @dynamic protocol for runtime dispatch",
                    record
                )

    def _check_override_annotations(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Validate @override-annotated methods.

        Errors if the method does not exist in any parent class or implemented protocol.
        Warns if the override is non-polymorphic (parent class, not @dynamic protocol).
        """
        override_methods = {m.name: m for m in record.methods if m.is_override}

        for method_name, method in override_methods.items():
            found_in_parent = self._find_mro_ancestor_with_method(record_info, method_name)

            # Check each explicitly implemented protocol for the method
            found_in_protocol: str | None = None
            found_in_dynamic_protocol = False
            for proto_type in record_info.implemented_protocols:
                all_proto_methods = self.protocols.collect_protocol_methods(proto_type.name)
                if any(m.name == method_name for m in all_proto_methods):
                    found_in_protocol = proto_type.name
                    found_in_dynamic_protocol = getattr(proto_type, 'is_dynamic_protocol', False)
                    break

            if not found_in_parent and not found_in_protocol:
                raise SemanticError(
                    f"Method '{record.name}.{method_name}' is marked @override "
                    f"but does not override any parent class or protocol method",
                    method.loc or record.loc,
                )

            # Non-polymorphic warning: only for parent class overrides (not protocol implementations).
            # Constructors/destructors are skipped -- they are never polymorphically dispatched.
            if found_in_parent and method_name not in ("__init__", "__del__"):
                already_dynamic = found_in_dynamic_protocol
                suffix = "" if already_dynamic else " Use a @dynamic protocol for runtime dispatch."
                self.ctx.warning_from_loc(
                    f"Method '{record.name}.{method_name}' overrides '{found_in_parent}.{method_name}' "
                    f"but the override is non-polymorphic. '{found_in_parent}'-typed references "
                    f"will call '{found_in_parent}.{method_name}', not '{record.name}.{method_name}'."
                    f"{suffix}",
                    method.loc or record.loc,
                )

    def _find_mro_ancestor_with_method(self, record_info: RecordInfo, method_name: str) -> str | None:
        """Find the nearest MRO ancestor (excluding record_info itself) that defines method_name."""
        for anc_rec in self.ctx.registry.iter_ancestor_records(record_info):
            # No reference typed as a non-base ancestor can hold this record,
            # so there is nothing for a same-named method to hide or override.
            if not self.ctx.registry.is_struct_base(record_info, anc_rec):
                continue
            if anc_rec.get_method(method_name) is not None:
                return anc_rec.name
        return None

    def _method_is_transitively_dynamic_protocol_member(
            self, record_info: RecordInfo, method_name: str) -> bool:
        """True iff method_name appears in a @dynamic protocol implemented by
        the record or any ancestor (transitively).

        Used to suppress the "method hides ancestor" warning when codegen will
        emit the method as `override` on a virtual @dynamic-protocol slot.
        """
        for proto, _ in self.ctx.registry.iter_dynamic_protocols(record_info):
            for sig in self.protocols.collect_protocol_methods(proto.name):
                if sig.name == method_name:
                    return True
        return False

    def _is_inheritable_builtin(self, typ: TpyType) -> bool:
        """Check if a type is a builtin type that can be inherited from."""
        qname = typ.qualified_name()
        if qname is None:
            return False
        return self.ctx.registry.get_builtin_record(qname) is not None

    def register_protocol(self, protocol: TpyProtocol) -> None:
        """Register a protocol type (without validating parents yet)."""
        # Resolve implicit readonly and cross-module protocol flags on method
        # signatures. resolve_type backfills is_protocol / _module_qname on
        # NominalType references to other protocols (e.g. Iterator[T] in
        # Iterable[T].__iter__) so that later signature matches can detect
        # protocol returns.
        resolved_methods = []
        for msig in protocol.methods:
            resolved_readonly = msig.is_readonly or (
                msig.name in IMPLICIT_READONLY_METHODS and not msig.readonly_opt_out
            )
            resolved_params = [
                (n, self.type_ops.resolve_type(t, protocols_only=True))
                for n, t in msig.params
            ]
            resolved_return = self.type_ops.resolve_type(msig.return_type, protocols_only=True)
            resolved_methods.append(MethodSignature(
                name=msig.name,
                params=resolved_params,
                return_type=resolved_return,
                is_readonly=resolved_readonly,
                readonly_opt_out=msig.readonly_opt_out,
                cpp_template=msig.cpp_template,
                param_defaults=msig.param_defaults,
                num_posonly_params=msig.num_posonly_params,
            ))
        info = ProtocolInfo(
            name=protocol.name,
            methods=resolved_methods,
            fields=protocol.fields,
            type_params=protocol.type_params,
            parent_protocols=protocol.parent_protocols,
            # Parser stores the raw cpp_concept string; the `::`-prefix
            # normalization happens here so parser does not need to
            # import `typesys.ensure_qualified`.
            cpp_concept=ensure_qualified(protocol.cpp_concept) if protocol.cpp_concept else None,
            is_marker=protocol.cpp_concept is not None and len(resolved_methods) == 0,
            is_dynamic=protocol.is_dynamic,
            module=public_module_name(self.ctx.module_name, self.ctx.module_cpp_namespace),
        )
        # Cycle peers' bind_imports captures a reference to a
        # pre-populated ProtocolInfo skeleton; mutate it in place so
        # peers see the freshly-finalized methods / parents.
        decl_exports = self.ctx.module_decl_exports
        if decl_exports is not None:
            existing_skeleton = decl_exports.protocols.get(protocol.name)
            if existing_skeleton is not None:
                info = _adopt_skeleton(existing_skeleton, info)
        self.ctx.registry.register_protocol(info)
        install_binding(
            self.ctx.module_attributes, protocol.name,
            protocol_kind_for(protocol.is_dynamic), info,
        )
        # Attach ProtocolInfo to the TypeDef registry under a stable qname.
        # User protocols in entry-point modules fall back to `__main__.<name>`,
        # mirroring the record/enum convention.
        qname_module = info.module if info.module else "__main__"
        attach_dynamic_type_def(
            f"{qname_module}.{info.name}",
            TypeCategory.PROTOCOL,
            protocol=info,
        )

    def finalize_protocol_closure(self, protocol: TpyProtocol) -> None:
        """Populate ``ProtocolInfo.transitive_supertypes`` for one protocol.

        Must run after every protocol in the current module is registered
        so in-module parents resolve via ``scan_by_short_name``; cross-
        module parents resolve too because modules are compiled in
        topological order. The ``visited`` guard makes a malformed
        cycle in ``parent_protocols`` (rejected elsewhere in sema, but
        cheap to defend here) terminate instead of hang.
        """
        info = self.ctx.registry.scan_by_short_name(protocol.name)
        if info is None:
            return
        ancestors: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = [p.name for p in info.parent_protocols]
        while stack:
            name = stack.pop()
            if name in visited:
                continue
            visited.add(name)
            ancestors.add(name)
            parent_info = self.ctx.registry.scan_by_short_name(name)
            if parent_info is None:
                continue
            stack.extend(p.name for p in parent_info.parent_protocols)
        info.transitive_supertypes = frozenset(ancestors)

    def validate_protocol_parents(self, protocol: TpyProtocol) -> None:
        """Validate that all parent protocols are actual protocols.

        Called after all protocols are registered to allow forward references.
        Also validates object safety for @dynamic protocols over the full inherited
        surface (methods + fields from all ancestor protocols).
        """
        for parent in protocol.parent_protocols:
            parent_info = self.ctx.registry.scan_by_short_name(parent.name)
            if parent_info is None:
                raise SemanticError(
                    f"Protocol '{protocol.name}' inherits from '{parent.name}', "
                    f"which is not a defined protocol",
                    protocol.loc
                )
            # Generic-arity mismatches (missing args, wrong count) are caught
            # earlier by the type-ref resolver. The only case that reaches here
            # is type args supplied to a non-generic parent.
            if not parent_info.type_params and parent.type_args:
                raise SemanticError(
                    f"Protocol '{protocol.name}' inherits from non-generic protocol "
                    f"'{parent.name}' with {len(parent.type_args)} type argument(s)",
                    protocol.loc
                )

        if protocol.is_dynamic:
            self._validate_dynamic_object_safety(protocol)

    def _validate_dynamic_object_safety(self, protocol: TpyProtocol) -> None:
        """Validate that a @dynamic protocol is object-safe for runtime dispatch.

        Checks the full inherited surface (methods + fields from all ancestors),
        since codegen emits all inherited members into the C++ base class.
        """
        all_methods = self.protocols.collect_protocol_methods(protocol.name)
        all_fields = self.protocols.collect_protocol_fields(protocol.name)

        # Markerless @dynamic protocols (no methods, no fields) are allowed:
        # they're phylum tags used by the polymorphism predicate to gate
        # things like `Optional[E]` class dispatch via dynamic_cast. The
        # emitted C++ shape is an empty abstract base struct with just a
        # virtual destructor + a trivially-satisfied concept; Adapter and
        # RefAdapter wrap anything. No actual dispatch goes through the
        # vtable in the marker case -- it's purely a sema-level tag.

        for msig in all_methods:
            if _contains_self_type(msig.return_type):
                raise SemanticError(
                    f"@dynamic protocol '{protocol.name}' cannot use Self type "
                    f"in method '{msig.name}' return type",
                    protocol.loc
                )
            for pname, ptype in msig.params:
                if _contains_self_type(ptype):
                    raise SemanticError(
                        f"@dynamic protocol '{protocol.name}' cannot use Self type "
                        f"in method '{msig.name}' parameter '{pname}'",
                        protocol.loc
                    )

        for field_name, field_type in all_fields:
            if _contains_self_type(field_type):
                raise SemanticError(
                    f"@dynamic protocol '{protocol.name}' cannot use Self type "
                    f"in field '{field_name}'",
                    protocol.loc
                )

    def _stamp_iterator_retention(self, func: TpyFunction, info: FunctionInfo) -> None:
        """Set `return_borrows_from` at registration for a callee whose result
        keeps its reference arguments: a generator or coroutine (its frame),
        or a body-less lazy combinator (`zip`, `enumerate`, `filter`, ...).

        Exact and signature-derived, so a caller whose body is analyzed
        before the generator's still sees the frame's reference captures
        (the auto-move gate would otherwise miss them); finalize unions the
        body-derived facts on top. A combinator has no body to derive the
        fact from at all, and its C++ object keeps its reference-typed
        arguments alive exactly as a frame does: the loop variable a caller
        binds off its result aliases those arguments, so a mutation through
        it has to climb to them.

        Indices are read against the CALL's argument list, so they come from
        the full parameter list -- a `*args` pack is absent from the resolved
        params and would both be missed and shift any keyword-only param
        behind it.
        """
        if (func.is_generator or func.is_async
                or (func.is_stub and iterator_source_callee(info))):
            info.return_borrows_from = self.generator_borrow_param_indices(
                [p.type for p in info.params])

    @staticmethod
    def generator_borrow_param_indices(
            param_types: 'list[TpyType]') -> frozenset[int]:
        """Param indices a generator's or coroutine's returned frame borrows:
        the frame
        stores non-value params as T& references and explicit view params
        (StrView/Span) as views, so the generator object borrows those
        arguments. `str` / `bytes` params are NOT borrowed -- they are captured
        OWNED in the frame (is_owned_in_coro_frame; codegen routes them to
        _CoroParamKind.OWNED_COPY), so excluding them here keeps this sema fact
        in step with codegen's frame storage. Signature-derived, so it is exact
        at registration time -- callers analyzed before the generator's body
        still see the right facts.

        A `*args` pack is value-typed but views the caller's argument array,
        so the frame borrows it exactly the way a Span param is borrowed.
        """
        return frozenset(
            i for i, ptype in enumerate(param_types)
            if (not ptype.is_value_type() or is_str_type(ptype)
                or is_borrowing_view_type(ptype) or is_varargs(ptype))
            and not is_owned_in_coro_frame(ptype)
        )

    def _validate_param_defaults(self, param_infos: 'list[ParamInfo]') -> None:
        """Type-check parameter defaults against their declared types.

        The parser accepts a default by shape alone; this is the type-aware
        gate, keeping parameter defaults consistent with field defaults. A
        `Final[T]` name resolves too late to see here and is checked in the
        analyzer instead."""
        for pi in param_infos:
            default = pi.default_expr
            if default is None:
                continue
            if not isinstance(default, TpyFieldAccess):
                check_default_value_type(default, pi.type, self.compat,
                                         default.loc, target_noun="parameter",
                                         target_name=pi.name)
                continue
            # An enum-member default is monomorphic: it is only meaningful when
            # the parameter type IS that concrete enum. A type-param-containing
            # type (bare `T`, `T | None`, ...) can't be checked here and would
            # emit a `T x = Enum::M` initializer C++ rejects opaquely at
            # instantiation, so reject it up front (unlike a literal default,
            # which is legitimately polymorphic and deferred to instantiation).
            if contains_type_param(pi.type):
                raise SemanticError(
                    f"an enum-member default requires a concrete enum-typed "
                    f"parameter; parameter '{pi.name}' has generic type "
                    f"'{pi.type}'", default.loc)
            if not check_enum_member_default(
                    default, pi.type, self.ctx.registry, default.loc,
                    target_noun="parameter"):
                raise SemanticError(
                    f"default value '{default.obj.name}.{default.field}' is not "
                    f"a resolvable enum member", default.loc)

    def validate_ast_param_defaults(self, func: TpyFunction) -> None:
        """Type-check a function's defaults straight off the AST.

        For a callable that never reaches `register_function` and so builds no
        ParamInfo -- the @overload IMPLEMENTATION, whose defaults codegen still
        emits into each specialization. A `Final[T]` name is not judged here:
        `_validate_named_defaults` covers it during body analysis, which the
        implementation does undergo.
        """
        defaults = func.defaults or []
        for i, (pname, ptype) in enumerate(func.params):
            default = defaults[i] if i < len(defaults) else None
            if default is None:
                continue
            try:
                resolved = self.type_ops.resolve_type(ptype)
            except SemanticError:
                continue  # a bad annotation is reported by its own pass
            check_default_value_type(default, resolved, self.compat,
                                     default.loc, target_noun="parameter",
                                     target_name=pname)

    def register_function(self, func: TpyFunction, *, unique_declaration: bool = False) -> None:
        """Register a function."""
        # Allow TypeParamRef in params/return for generic functions
        is_generic = bool(func.type_params)

        # Resolve types (sets is_protocol flag correctly for imported protocols)
        resolved_params = []
        for pname, ptype in func.params:
            try:
                resolved_ptype = self.type_ops.resolve_type(ptype)
                self.type_ops.validate_type(resolved_ptype, allow_type_param_ref=is_generic,
                                            allow_pointer_repr_dynamic=True)
            except SemanticError as e:
                raise self.ctx.error(str(e), func)
            if _contains_self_type(resolved_ptype):
                raise SemanticError(
                    f"Self type cannot be used in function parameter '{pname}'. "
                    f"Self is only valid in class or protocol method signatures",
                    func.loc
                )
            resolved_params.append((pname, resolved_ptype))

        # Resolve *args parameter: append as Span[readonly[T]]
        resolved_vararg_type = None
        if func.vararg_name is not None:
            resolved_vararg_type = self.type_ops.resolve_type(func.vararg_type)
            self.type_ops.validate_type(resolved_vararg_type, allow_type_param_ref=is_generic)

        resolved_return = self.type_ops.resolve_type(func.return_type)
        try:
            self.type_ops.validate_type(resolved_return, allow_type_param_ref=is_generic)
        except SemanticError as e:
            raise self.ctx.error(str(e), func)

        if _contains_self_type(resolved_return):
            raise SemanticError(
                f"Self type cannot be used as a return type. "
                f"Self is only valid in class or protocol method signatures",
                func.loc
            )

        # Protocol types cannot be used as return types (but TypeParamRef is OK).
        # Exception: @dynamic protocols can be returned (lifetime-checked in sema).
        # Exception: generator functions return Iterator[T] (codegen emits concrete struct).
        # Exception: @native/@cpp_template stubs (C++ handles the actual return type).
        if is_protocol_type(resolved_return):
            if func.is_generator:
                # Validate that it's Iterator[T]
                if resolved_return.qualified_name() != "typing.Iterator":
                    raise SemanticError(
                        f"Generator function must have return type 'Iterator[T]', "
                        f"got '{resolved_return}'",
                        func.loc
                    )
                if not resolved_return.type_args:
                    raise SemanticError(
                        f"Iterator must have a type argument, e.g. Iterator[int32]",
                        func.loc
                    )
                func.generator_yield_type = resolved_return.type_args[0]
                self._validate_generator_yield_copyable(
                    func.generator_yield_type, func.loc)
            else:
                pi = protocol_info_of(resolved_return)
                is_native_stub = func.is_stub and (func.native_name or func.cpp_template)
                if not (pi and pi.is_dynamic) and not is_native_stub:
                    raise SemanticError(
                        f"Protocol type '{resolved_return.name}' cannot be used as a return type. "
                        f"Only @dynamic protocols can be used as return types",
                        func.loc
                    )

        # Generator without Iterator[T] return type
        if func.is_generator and not is_protocol_type(resolved_return):
            raise SemanticError(
                f"Generator function must have return type 'Iterator[T]', "
                f"got '{resolved_return}'",
                func.loc
            )

        # *args on async def: the await-call lowering for a variadic coroutine
        # factory is not wired -- the vararg pack reaches gen_async's coro
        # emplace path with no argument and miscompiles to opaque C++. Reject
        # at the source rather than at the C++ build.
        if func.is_async and func.vararg_name is not None:
            raise SemanticError(
                "Variadic positional parameters (*args) are not yet supported "
                "on async functions",
                func.loc
            )

        if contains_fn_type(resolved_return):
            raise SemanticError(
                "Fn type is only valid in parameter position. "
                "Use Callable for fields, returns, and locals",
                func.loc
            )

        # Fn is valid as a bare param type but not nested inside Optional/Union
        for pname, ptype in func.params:
            if not is_fn_type(ptype) and contains_fn_type(ptype):
                raise SemanticError(
                    f"Fn type cannot be nested inside another type (Optional, Union, list, etc.). "
                    f"Use Callable for parameter '{pname}' instead",
                    func.loc
                )

        type_param_bounds = self._resolve_type_param_bounds(
            func.type_param_bounds, func.loc, type_params=list(func.type_params))
        # Propagate resolved bounds back to AST so get_type_param_bound sees
        # correct is_protocol flags during body analysis (safe: fresh AST per compile)
        if type_param_bounds:
            func.type_param_bounds.update(type_param_bounds)

        fi_linkage = _LINKAGE_MAP[func.linkage.name]

        func_defaults = func.defaults if func.defaults else []
        param_infos = []
        kw_start = func.keyword_only_start
        for i, (n, t) in enumerate(resolved_params):
            is_kwonly = kw_start is not None and i >= kw_start
            # Insert *args param before keyword-only params
            if is_kwonly and func.vararg_name is not None and resolved_vararg_type is not None and i == kw_start:
                span_type = _vararg_span_type(resolved_vararg_type)
                param_infos.append(ParamInfo(func.vararg_name, span_type, is_variadic=True))
            param_infos.append(ParamInfo(n, t,
                      default_expr=func_defaults[i] if i < len(func_defaults) else None,
                      keyword_only=is_kwonly,
                      positional_only=i < func.num_posonly_params))
        # *args with no keyword-only params: append at end
        if func.vararg_name is not None and resolved_vararg_type is not None and (kw_start is None or kw_start >= len(resolved_params)):
            span_type = _vararg_span_type(resolved_vararg_type)
            param_infos.append(ParamInfo(func.vararg_name, span_type, is_variadic=True))

        self._validate_param_defaults(param_infos)

        # **kwargs: Unpack[TypedDict] -- append as TypedDict param at end
        resolved_kwarg_type = None
        if func.kwarg_name is not None and func.kwarg_type is not None:
            resolved_kwarg_type = self.type_ops.resolve_type(func.kwarg_type)
            # Validate no field name conflicts with regular params
            if isinstance(resolved_kwarg_type, NominalType):
                td_record = self.ctx.registry.get_record(resolved_kwarg_type.name)
                if td_record is not None:
                    param_names = {n for n, _ in resolved_params}
                    for fld in td_record.fields:
                        if fld.name in param_names:
                            raise SemanticError(
                                f"TypedDict field '{fld.name}' conflicts with "
                                f"parameter '{fld.name}' on '{func.name}'",
                                func.loc,
                            )
            param_infos.append(ParamInfo(func.kwarg_name, resolved_kwarg_type,
                                         is_kwargs=True))

        # async def f() -> T: callers see Cancellable[T] (mirrors how
        # generators surface as Iterator[T]). The user's T stays on the
        # AST node (`func.return_type` at line 2531 below) so codegen and
        # the body-return checker keep seeing T; the FunctionInfo carries
        # Cancellable[T] so structural matching at call sites
        # (`create_task(f())`, `wait_for(f())`, etc.) honors the
        # cancellability that gen_async.py auto-emits on every coro
        # frame. Awaitable-only consumers (`await`, `async for`,
        # `async with`) still match via Cancellable's `__poll__` member.
        if func.is_async:
            # `async def f() -> None` resolves the function return type
            # to VoidType (top-level return-shape), but Cancellable[T] is
            # a type-arg position where None lowers to NoneType. Convert
            # at the boundary so `t: Task[None] = create_task(f())` matches.
            inner = NONE if isinstance(resolved_return, VoidType) else resolved_return
            fi_return_type = make_cancellable(inner)
        else:
            fi_return_type = resolved_return
        info = FunctionInfo(
            name=func.name,
            declaration=func if unique_declaration else None,
            params=param_infos,
            return_type=fi_return_type,
            async_inner_return=resolved_return if func.is_async else None,
            is_noalloc=func.is_noalloc,
            is_readonly=func.is_readonly or func.is_pure,
            is_pure=func.is_pure,
            is_inline=func.is_inline,
            is_async=func.is_async,
            is_generator=func.is_generator,
            send_override=func.send_override,
            sync_override=func.sync_override,
            linkage=fi_linkage,
            native_name=func.native_name,
            native_cpp_return_type=func.native_cpp_return_type,
            cpp_template=func.cpp_template,
            value_ptr_coercion=func.value_ptr_coercion,
            type_params=func.type_params,
            type_param_bounds=type_param_bounds,
            type_param_defaults=func.type_param_defaults,
            is_builtin_function=bool(func.builtin_function_key),
            special_handling=bool(func.builtin_function_key),
            error_return_type=(qualify_exception_name(func.error_return, self.ctx.registry,
                                                      self.ctx.module_name)
                               if func.error_return else None),
            qualified_name=(func.builtin_function_key
                            if func.builtin_function_key
                            else f"{self.ctx.module_name}.{func.name}"),
            kwarg_name=func.kwarg_name,
            # Stamp the defining module so cross-module mutation
            # propagation can gate ownership precisely. Opaque /
            # builtin FIs leave this None so the gate excludes them.
            originating_module=(None if func.builtin_function_key
                                else self.ctx.module_name),
        )
        self._stamp_iterator_retention(func, info)
        # @inline: store body for call-site inlining
        if func.is_inline and not func.is_stub:
            non_doc = [s for s in func.body
                       if not (isinstance(s, TpyExprStmt)
                               and isinstance(s.expr, TpyStrLiteral))]
            if (len(non_doc) == 1
                    and isinstance(non_doc[0], TpyExprStmt)
                    and isinstance(non_doc[0].expr, (TpyCall, TpyMethodCall))):
                info.inline_body = non_doc[0].expr
            else:
                raise SemanticError(
                    f"@inline function '{func.name}' must have a single "
                    f"call expression as its body.",
                    func.loc,
                )
        elif any(is_fstr_type(p.type) for p in info.params) and not func.is_stub:
            raise SemanticError(
                f"Function '{func.name}' has FStr parameter but is not marked @inline. "
                f"FStr parameters require @inline.",
                func.loc,
            )

        # Propagate qualified name back to AST so codegen can use it directly
        if func.error_return:
            orig_name = func.error_return
            func.error_return = info.error_return_type
            # @error_return(E) requires E to be a ReturnException type
            if not is_return_exception(info.error_return_type):
                raise SemanticError(
                    f"'{orig_name}' is not a ReturnException type; "
                    f"@error_return requires a ReturnException exception",
                    func.loc
                )

        # Check for duplicate extern symbol names
        if fi_linkage != FunctionLinkage.DEFAULT:
            symbol = func.native_name or func.name
            if symbol in self.ctx.extern_symbols:
                prev = self.ctx.extern_symbols[symbol]
                raise self.ctx.error(
                    f"Duplicate extern symbol '{symbol}' "
                    f"(already declared by '{prev}')",
                    func
                )
            self.ctx.extern_symbols[symbol] = func.name

        # C-linkage signatures are emitted verbatim into an `extern "C"`
        # declaration, so every type in them has to be one a C caller can
        # spell. Outside the allow-list the emit is either ill-formed C++
        # or -- worse -- compiles to a signature no C caller can call.
        if fi_linkage in (FunctionLinkage.NATIVE_C, FunctionLinkage.EXPORT_C):
            for pname, ptype in resolved_params:
                if not is_c_abi_allowed(ptype):
                    raise self.ctx.error(
                        f"parameter '{pname}': type '{ptype}' "
                        f"{C_ABI_TYPE_ERROR}; {c_abi_type_hint(ptype)}",
                        func
                    )
            if func.vararg_name is not None:
                raise self.ctx.error(
                    f"'*{func.vararg_name}': variadic parameters are not "
                    f"supported on a C-linkage function",
                    func
                )
            if not is_c_abi_allowed(resolved_return, is_return=True):
                raise self.ctx.error(
                    f"return type '{resolved_return}' {C_ABI_TYPE_ERROR}; "
                    f"{c_abi_type_hint(resolved_return)}",
                    func
                )

        # If the Compiler pre-populated a skeleton FunctionInfo for
        # `func.name` (so cycle peers' bind_imports could find it
        # before our sub-phase 3 ran), mutate the skeleton in place
        # and use it instead of `info`. Peer registries that captured
        # the skeleton via `_register_user_module_import` see the
        # freshly-finalized fields without a post-hoc resync.
        decl_exports = self.ctx.module_decl_exports
        if decl_exports is not None:
            existing = decl_exports.functions.get(func.name)
            if existing and len(existing) == 1:
                info = _adopt_skeleton(existing[0], info)

        self.ctx.registry.register_function(info)
        self.ctx.global_ns.bind_function(info)
        # Per-module attribute table (Phase 1). Use the registry's list
        # so binding.info shares identity with the freshly-registered
        # one (which `_extract_declaration_exports` will copy into
        # `compiled.exports.functions`).
        install_binding(
            self.ctx.module_attributes, info.name,
            SymbolKind.FUNCTION,
            self.ctx.registry.get_function(info.name),
        )

        # Propagate resolved types back to AST (matches register_record and
        # register_overload_group). For non-stub functions, _analyze_function
        # will re-resolve and wrap with make_ref. For stubs, this is the only
        # site that substitutes parser-level placeholders (imported enums,
        # user records) with qname-bearing types the codegen expects.
        func.params = list(resolved_params)
        func.return_type = resolved_return

    def register_overload_group(self, stubs: list[TpyFunction]) -> None:
        """Register a group of @overload stubs as a single overloaded function binding.

        Each stub is resolved and validated. All stubs are bound together so
        call-site resolution can pick the best match.
        """
        infos: list[FunctionInfo] = []
        for func in stubs:
            is_generic = bool(func.type_params)
            resolved_params = []
            for pname, ptype in func.params:
                resolved_ptype = self.type_ops.resolve_type(ptype)
                try:
                    self.type_ops.validate_type(resolved_ptype, allow_type_param_ref=is_generic,
                                                allow_pointer_repr_dynamic=True)
                except SemanticError as e:
                    raise self.ctx.error(str(e), func)
                resolved_params.append((pname, resolved_ptype))

            resolved_return = self.type_ops.resolve_type(func.return_type)
            try:
                self.type_ops.validate_type(resolved_return, allow_type_param_ref=is_generic)
            except SemanticError as e:
                raise self.ctx.error(str(e), func)

            type_param_bounds = self._resolve_type_param_bounds(
                func.type_param_bounds, func.loc)
            # Propagate resolved bounds back to AST (safe: fresh AST per compile)
            if type_param_bounds:
                func.type_param_bounds.update(type_param_bounds)

            func_defaults = func.defaults if func.defaults else []
            stub_param_infos = [
                ParamInfo(n, t,
                          default_expr=func_defaults[i] if i < len(func_defaults) else None,
                          keyword_only=(func.keyword_only_start is not None
                                        and i >= func.keyword_only_start),
                          positional_only=i < func.num_posonly_params)
                for i, (n, t) in enumerate(resolved_params)
            ]
            if func.vararg_name is not None and func.vararg_type is not None:
                va_type = self.type_ops.resolve_type(func.vararg_type)
                stub_param_infos.append(
                    ParamInfo(func.vararg_name, _vararg_span_type(va_type),
                              is_variadic=True))
            # A stub's own defaults reach codegen (the specialization is
            # synthesized from the STUB's params, not the implementation's),
            # so they need the same gate a plain function's get.
            self._validate_param_defaults(stub_param_infos)
            info = FunctionInfo(
                name=func.name,
                params=stub_param_infos,
                return_type=resolved_return,
                is_noalloc=func.is_noalloc,
                is_readonly=func.is_readonly or func.is_pure,
                is_pure=func.is_pure,
                linkage=_LINKAGE_MAP[func.linkage.name],
                native_name=func.native_name,
                native_cpp_return_type=func.native_cpp_return_type,
                cpp_template=func.cpp_template,
                type_params=func.type_params,
                type_param_bounds=type_param_bounds,
                type_param_defaults=func.type_param_defaults,
                qualified_name=f"{self.ctx.module_name}.{func.name}",
                originating_module=self.ctx.module_name,
            )
            self._stamp_iterator_retention(func, info)
            # Propagate resolved types back to AST (matches register_record behavior)
            func.params = list(resolved_params)
            func.return_type = resolved_return
            infos.append(info)

        if infos:
            self._reject_same_param_overloads(infos, stubs)
            # Adopt the pre-populated skeleton list (if any) by mutating
            # it in place. Cycle peers' bind_imports may have captured a
            # reference to that single-element placeholder list before
            # this overload group ran; mutating its contents here
            # propagates the freshly-finalized FunctionInfos to those
            # captured references. Without this the peer's analyzer
            # registry stays bound to an empty FI and overload
            # resolution at peer call sites picks the placeholder.
            decl_exports = self.ctx.module_decl_exports
            if decl_exports is not None:
                skeleton_list = decl_exports.functions.get(infos[0].name)
                if skeleton_list is not None:
                    skeleton_list.clear()
                    skeleton_list.extend(infos)
                    infos = skeleton_list
            self.ctx.registry.register_function_group(infos[0].name, infos)
            self.ctx.global_ns.bind(NameBinding(
                kind=BindingKind.FUNCTION,
                name=infos[0].name,
                func_infos=infos,
            ))
            install_binding(
                self.ctx.module_attributes, infos[0].name,
                SymbolKind.FUNCTION,
                self.ctx.registry.get_function(infos[0].name),
            )

    def _reject_same_param_overloads(
        self,
        infos: list[FunctionInfo],
        nodes: list[TpyFunction] | None,
        is_method: bool = False,
    ) -> None:
        """Reject overload sets where two members have identical
        positional + keyword-only parameter types.

        The C++ realization mangles the function by parameter shape, not
        return type, so two overloads with the same parameter signature
        produce an "ambiguating new declaration" linker error. Catch
        this at sema with a pointed diagnostic instead of letting the
        C++ compiler complain.

        For methods, two overloads that share param types but differ in
        ``is_readonly`` or ``is_consuming`` are legitimate const-qualified
        variants (auto_readonly / auto_own) -- C++ emits them with ``&``
        / ``const &`` / ``&&`` qualifiers and there's no ambiguity. The
        check skips those.
        """
        seen: dict[tuple, int] = {}
        for i, info in enumerate(infos):
            key_parts: list = [
                (p.type, p.keyword_only)
                for p in info.params
                if not p.is_variadic
            ]
            if is_method:
                key_parts.append(info.is_readonly)
                key_parts.append(info.is_consuming)
            key = tuple(key_parts)
            prev = seen.get(key)
            if prev is not None:
                params_str = ", ".join(str(p.type) for p in info.params if not p.is_variadic)
                node = nodes[i] if nodes and i < len(nodes) else None
                prev_loc_hint = ""
                if nodes and prev < len(nodes):
                    prev_node = nodes[prev]
                    prev_loc = self.ctx._resolve_loc(prev_node)
                    if prev_loc is not None:
                        prev_loc_hint = f" (first defined at line {prev_loc.line})"
                decorator = nodes[0].overload_form.value if nodes else "overload"
                raise self.ctx.error(
                    f"@{decorator} variants of '{info.name}' have identical "
                    f"parameter types ({params_str}){prev_loc_hint}; overloads "
                    f"must differ in at least one parameter type, not just "
                    f"the return type",
                    node,
                )
            seen[key] = i

    def register_globals(self, stmts: list[TpyStmt]) -> None:
        """Register top-level variable declarations in global scope.

        Only registers explicitly typed globals here. Untyped globals are
        fully analyzed in analyze_top_level, which provides proper context
        for list literal type inference.
        """
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type:
                actual_type = stmt.type
                # Detect Final[T]: unwrap, record finality, register inner type
                if isinstance(actual_type, FinalType):
                    actual_type = final_type_str_to_strview(actual_type.wrapped)
                    stmt.is_final = True
                    self.ctx.final_globals.add(stmt.name)
                # This walk is over the module's statement list itself, so
                # every decl it sees is a module slot by construction.
                self.ctx.define_module_global(
                    stmt.name, actual_type, stmt.loc.line if stmt.loc else 0)
                self.ctx.preregistered_globals.add(stmt.name)
                self.ctx.global_ns.bind_variable(stmt.name, actual_type)
                install_binding(
                    self.ctx.module_attributes, stmt.name,
                    SymbolKind.VARIABLE, actual_type,
                )
