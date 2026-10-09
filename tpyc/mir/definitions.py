"""Immutable, verified constructor definitions supplied by the THIR caller,
and the builtin certificates of owned leaves' opaque storage."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.scalar_leaves import (
    container_view, declared_members, holds_loan, inline_member_type, leaf_constant, loan_free, modeled_field,
    native_container_type, optional_record_field, owned_constant, owned_leaf, owned_value_type, plain_record_element,
    record_type, storage_leaf, view_compatible, view_leaf,
)
from ..type_def_registry import ParamPassing, type_def_of
from ..typesys import NominalType, OptionalType, OwnType, ReadonlyType, TpyType, unwrap_readonly, unwrap_ref_type
from .call_contract import BORROWING_PASSINGS, OWNING_PASSINGS
from .coverage import MIRUnsupported, literal_type, plain, require, scalar_param
from .nodes import (
    MIRConstant, MIRContainerLayout, MIRField, MIRFieldId, MIRMemberInitMode, MIROptionalConstruct, MIRRecordLayout,
    MIRTupleElement, MIRValueKind,
)


@dataclass(frozen=True)
class MIRComposedConstruct:
    """A record member built by its own constructor: the member record's
    verified definition composed with the arguments the member initializer
    passes, one initializer per member field in its layout order, sourced
    from the outer constructor's parameters and constants."""
    initializers: tuple['MIRFieldInitializer', ...]


@dataclass(frozen=True)
class MIRFieldInitializer:
    """How the member initializer list fills one field: from a constructor
    parameter (named) or a constant, by value (an inert leaf), by copy or by
    move (an owned leaf or a record). Decided once here; the constructor
    body and every caller read it."""
    field: MIRField
    # A container field may also be a literal over parameters, one name per
    # element (a dict's keys and values alternating), moved in once built;
    # a record field a construct of its own constructor; an Optional record
    # field left empty (`MIROptionalConstruct()`) or engaged with a payload
    # its own constructor builds.
    source: str | MIRConstant | tuple[str, ...] | MIRComposedConstruct | MIROptionalConstruct
    mode: MIRMemberInitMode
    # The copy (or a constant's materialization) can exit by exception.
    may_raise: bool
    loc: SourceLocation | None = None


def view_members(layout: MIRRecordLayout | None) -> tuple[MIRField, ...]:
    """The view members of a record layout: each stores a loan the record
    object holds."""
    return () if layout is None else tuple(f for f in layout.fields if view_leaf(f.type))


def owned_parameter(p: th.THIRParam) -> bool:
    """A parameter holding an owned leaf lent (CONST_REF or VIEW: a readonly
    borrow of the caller's storage) or handed over (VALUE or OWN: the
    callee's own copy or the moved value); any other passing is refused."""
    return (owned_value_type(p.type) is not None and p.passing is not None
            and p.passing in BORROWING_PASSINGS | OWNING_PASSINGS)


def owned_record_parameter(p: th.THIRParam) -> NominalType | None:
    """The record an `Own[R]` parameter is handed over at OWN: the callee's
    own storage, moved in by the caller. None for anything else, a
    readonly-wrapped record included."""
    bare = p.type.wrapped if isinstance(p.type, OwnType) else None
    return (bare if p.passing is ParamPassing.OWN and p.borrowed_record is None
            and isinstance(bare, NominalType) and record_type(bare) else None)


def optional_parameter(p: th.THIRParam) -> NominalType | None:
    """The payload record of an Optional record parameter (`P | None`): the
    caller's pointer at the payload it lends readonly, or null. None for
    anything else."""
    layout = p.optional_layout
    payload = layout.payload if isinstance(layout, th.THIROptionalLayout) else None
    typ = unwrap_readonly(unwrap_ref_type(p.type))
    return (payload.type if isinstance(payload, th.THIRBorrowedRecord) and payload.readonly
            and p.passing is ParamPassing.POINTER and p.borrowed_record is None
            and isinstance(typ, OptionalType) and not typ.force_pointer_repr and typ.inner == payload.type
            and record_type(payload.type) else None)


def view_parameter(p: th.THIRParam) -> bool:
    """A parameter holding a view of an owned leaf (`StrView`), passed by
    value: the caller's loan, which the callee holds."""
    return view_leaf(p.type) and p.passing is ParamPassing.VALUE


def lends_view(p: th.THIRParam, view: TpyType) -> bool:
    """Whether a parameter holds a loan a `view` member may store: a view
    parameter's own, or a borrow of an owned-leaf parameter the caller lends.
    A parameter the callee owns (by value or `Own[str]`) dies with the call."""
    return (view_parameter(p) and view_compatible(view, p.type)
            or owned_parameter(p) and p.passing in BORROWING_PASSINGS
            and view_compatible(view, owned_value_type(p.type)))


def record_parameter(p: th.THIRParam) -> bool:
    """A constructor parameter holding a record: lent readonly at CONST_REF
    (the borrowed-record fact) or handed over at OWN (`Own[R]`). A record
    lent mutably has a writer the member initializer does not model."""
    ref = p.borrowed_record
    return (p.passing is ParamPassing.CONST_REF and ref is not None and ref.readonly
            and unwrap_readonly(unwrap_ref_type(p.type)) == ref.type
            or owned_record_parameter(p) is not None)


def _parameter_sources(source: str | MIRConstant | tuple[str, ...] | MIRComposedConstruct) -> set[str]:
    """The parameters an initializer's source names, through composed members."""
    match source:
        case str():
            return {source}
        case tuple():
            return set(source)
        case MIRComposedConstruct():
            return {name for init in source.initializers for name in _parameter_sources(init.source)}
    return set()


def layout_copy_may_raise(layout: MIRRecordLayout,
                          layout_of: Callable[[NominalType], MIRRecordLayout | None]) -> bool:
    """Whether copying storage of `layout` can exit by exception: some owned
    leaf in it copies a buffer that may (`TypeDef.copy_may_raise`), a
    container field copies its elements into a new allocation, or a member
    record's copy may (`layout_of`, recursively). A member whose layout is
    unknown may."""
    if layout.opaque:
        return bool(type_def_of(layout.type).copy_may_raise)
    for f in layout.fields:
        bare = inline_member_type(f.type)
        if owned_leaf(f.type) and type_def_of(f.type).copy_may_raise or native_container_type(bare):
            return True
        if record_type(bare):
            member = layout_of(bare)
            if member is None or layout_copy_may_raise(member, layout_of):
                return True
    return False


def _scalar_initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam], expected: TpyType) -> TpyType:
    require(expr, expr.form is th.Form.VALUE, "constructor initializer form")
    typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
    require(expr, storage_leaf(typ), "constructor initializer type")
    match expr:
        case th.THIRName():
            plain(expr, {"name", "is_last_use", "is_movable"})
            param = params.get(expr.name)
            require(expr, param is not None and param.type == typ, "constructor initializer needs parameter")
        case th.THIRLiteral():
            plain(expr, {"value", "int_cpp"})
            require(expr, leaf_constant(typ, expr.value), "constructor literal value")
        case th.THIRCoerce():
            plain(expr, {"expr", "coercion_name"})
            require(expr, expr.coercion_name == "int_literal_to_fixed_int"
                    and isinstance(expr.expr, th.THIRLiteral), "constructor coercion")
            require(expr, _scalar_initializer(expr.expr, params, typ) == typ, "constructor coercion type")
        case _:
            raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")
    return typ


def _literal_element(expr: th.THIRExpr, params: Mapping[str, th.THIRParam], member: TpyType) -> str:
    """A container literal's element in a member initializer: a parameter
    whose leaf the element slot copies (a view parameter's through its
    materialization)."""
    if isinstance(expr, th.THIRFormConvert):
        plain(expr, {"value", "is_const"})
        require(expr, expr.form is th.Form.STORAGE and isinstance(expr.value, th.THIRName)
                and expr.value.form is th.Form.BORROW, "constructor form conversion")
        expr = expr.value
    require(expr, isinstance(expr, th.THIRName), "constructor initializer needs parameter or literal")
    plain(expr, {"name", "is_last_use", "is_movable"})
    param = params.get(expr.name)
    require(expr, param is not None and (
        storage_leaf(member) and scalar_param(param) and param.type == member
        or owned_leaf(member) and owned_parameter(param) and owned_value_type(param.type) == member),
        "constructor initializer needs parameter")
    return param.name


def _container_initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam],
                           field: MIRField) -> MIRFieldInitializer:
    """A container member is copied from a container parameter, or moved
    from a literal built over parameters; either allocates."""
    typ = field.type
    match expr:
        case th.THIRName():
            plain(expr, {"name", "is_last_use", "is_movable"})
            param = params.get(expr.name)
            require(expr, param is not None and param.native_container is not None
                    and param.native_container.type == typ and expr.result_type == typ,
                    "constructor initializer needs parameter")
            return MIRFieldInitializer(field, param.name, MIRMemberInitMode.COPY, True, expr.loc)
        case th.THIRContainerLiteral():
            plain(expr, {"elements", "values", "typed_brace_cpp", "make_container", "elem_cpp", "bare_empty"})
            require(expr, expr.result_type == typ, "constructor field type")
            element, value, _ = declared_members(typ)
            operands: list[str] = []
            for i, e in enumerate(expr.elements):
                operands.append(_literal_element(e, params, unwrap_readonly(element)))
                if value is not None:
                    operands.append(_literal_element(expr.values[i], params, unwrap_readonly(value)))
            require(expr, value is None or len(expr.values) == len(expr.elements), "constructor field type")
            return MIRFieldInitializer(field, tuple(operands), MIRMemberInitMode.MOVE, True, expr.loc)
    raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")


def _verified_layout(definitions: 'Definitions') -> Callable[[NominalType], MIRRecordLayout | None]:
    def layout_of(typ: NominalType) -> MIRRecordLayout | None:
        definition = definitions.get(typ)
        return definition.layout if isinstance(definition, MIRConstructorDefinition) else None
    return layout_of


def _record_initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam], field: MIRField,
                        definitions: 'Definitions') -> MIRFieldInitializer:
    """A record member: copied from a record parameter lent readonly (spelled
    bare -- the implicit copy into storage -- or `copy(p)`), moved from an
    `Own[R]` parameter, or built by its own constructor over the outer
    constructor's parameters and literals (composed, as a base is)."""
    typ = unwrap_readonly(field.type)
    definition = definitions.get(typ)
    require(expr, isinstance(definition, MIRConstructorDefinition), "member definition: missing constructor definition")
    layout = definition.layout
    match expr:
        case th.THIRName() | th.THIRCopy():
            if isinstance(expr, th.THIRCopy):
                plain(expr, {"value", "cpp_type"})
                require(expr, expr.form is th.Form.STORAGE, "constructor copy form")
            name = expr.value if isinstance(expr, th.THIRCopy) else expr
            require(expr, isinstance(name, th.THIRName), "constructor initializer needs parameter")
            plain(name, {"name", "is_last_use", "is_movable"})
            param = params.get(name.name)
            require(expr, param is not None and record_parameter(param) and param.borrowed_record is not None
                    and param.borrowed_record.type == typ, "constructor initializer needs parameter")
            require(expr, layout.copyable, "constructor copies a noncopyable record")
            return MIRFieldInitializer(field, param.name, MIRMemberInitMode.COPY,
                                       layout_copy_may_raise(layout, _verified_layout(definitions)), expr.loc)
        case th.THIRMove():
            plain(expr, {"value"})
            name = expr.value
            require(expr, isinstance(name, th.THIRName), "constructor move needs parameter")
            plain(name, {"name", "is_last_use", "is_movable"})
            param = params.get(name.name)
            require(expr, param is not None and owned_record_parameter(param) == typ,
                    "constructor move needs an owned parameter")
            require(expr, layout.movable, "constructor moves a nonmovable record")
            return MIRFieldInitializer(field, param.name, MIRMemberInitMode.MOVE, False, expr.loc)
        case th.THIRCtorCall():
            return _composed_member(expr, params, field, typ, definition)
    raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")


def _composed_member(expr: th.THIRCtorCall, params: Mapping[str, th.THIRParam], field: MIRField,
                     typ: NominalType, definition: 'MIRConstructorDefinition') -> MIRFieldInitializer:
    """Record storage of `field` -- a record member, or an Optional record
    field's inline payload -- built by its own constructor over the outer
    constructor's parameters and literals (composed, as a base is), then
    moved in."""
    plain(expr, {"type_cpp", "args"})
    require(expr, expr.form is th.Form.STORAGE and unwrap_readonly(expr.result_type) == typ,
            "constructor field type")
    require(expr, len(expr.args) == len(definition.constructor.params),
            "member argument needs matching parameter or literal")
    require(expr, definition.layout.movable, "constructor moves a nonmovable record")
    initializers = _composed(expr, definition, expr.args, params, "member")
    return MIRFieldInitializer(field, MIRComposedConstruct(tuple(initializers)), MIRMemberInitMode.MOVE,
                               any(init.may_raise for init in initializers), expr.loc)


def _optional_initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam], field: MIRField,
                          definitions: 'Definitions') -> MIRFieldInitializer:
    """An Optional record member, its payload stored inline: copied from
    the payload an Optional record parameter lends (`ptr_to_optional`),
    left empty, or engaged with a payload its own constructor builds over
    the outer constructor's parameters and literals (composed)."""
    payload = optional_record_field(field.type)
    definition = definitions.get(payload)
    require(expr, isinstance(definition, MIRConstructorDefinition), "member definition: missing constructor definition")
    match expr:
        case th.THIRFormConvert():
            plain(expr, {"value", "is_const"})
            name = expr.value
            require(expr, expr.form is th.Form.STORAGE and isinstance(name, th.THIRName)
                    and name.form is th.Form.BORROW, "constructor form conversion")
            plain(name, {"name", "is_last_use", "is_movable", "optional_read"})
            param = params.get(name.name)
            require(expr, param is not None and optional_parameter(param) == payload
                    and name.optional_read == th.THIROptionalRead(param.optional_layout, False),
                    "constructor initializer needs parameter")
            require(expr, definition.layout.copyable, "constructor copies a noncopyable record")
            return MIRFieldInitializer(field, param.name, MIRMemberInitMode.COPY,
                                       layout_copy_may_raise(definition.layout, _verified_layout(definitions)), expr.loc)
        case th.THIRLiteral() if expr.value is None:
            plain(expr, {"value", "none_cpp"})
            require(expr, expr.form is th.Form.STORAGE, "constructor initializer form")
            return MIRFieldInitializer(field, MIROptionalConstruct(), MIRMemberInitMode.SCALAR, False, expr.loc)
        case th.THIRCtorCall():
            return _composed_member(expr, params, field, payload, definition)
    raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")


def _view_loan_source(expr: th.THIRExpr, params: Mapping[str, th.THIRParam], view: TpyType, *,
                      conversion: str, lent: str, literal: str) -> str | MIRConstant:
    """What a constructor's `view` slot (a view member, or a base's view
    parameter) borrows: the parameter whose loan it takes -- a view
    parameter, or a borrow of an owned-leaf parameter's lent storage
    (`coerce(%s -> StrView)` of a `str` view parameter) -- or a literal's
    static storage. A parameter the callee owns (by value or `Own[str]`)
    dies with the call, so it is never lent. Each refusal names its reason."""
    source = expr
    if isinstance(expr, th.THIRCoerce):
        plain(expr, {"expr", "coercion_name"})
        require(expr, expr.form is th.Form.BORROW and expr.result_type == view, conversion)
        source = expr.expr
    match source:
        case th.THIRName():
            plain(source, {"name", "is_last_use", "is_movable"})
            param = params.get(source.name)
            require(source, param is not None and lends_view(param, view), lent)
            return param.name
        case th.THIRStrLiteral():
            # A str literal is a `const char[N]` of static storage wherever a
            # view takes it; a bytes literal's default form builds an owned
            # temporary (`bytes_literal_owned`), so it stays refused.
            plain(source, {"value"})
            require(source, view_compatible(view, source.result_type)
                    and owned_constant(source.result_type, source.value), literal)
            return MIRConstant(source.value)
    raise MIRUnsupported(expr, lent)


def _view_initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam],
                      field: MIRField) -> MIRFieldInitializer:
    """A view member stores the loan its source lends (`_view_loan_source`)."""
    source = _view_loan_source(expr, params, field.type, conversion="constructor view conversion",
                               lent="constructor view needs a lent parameter", literal="constructor literal value")
    return MIRFieldInitializer(field, source, MIRMemberInitMode.BORROW, False, expr.loc)


def _initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam],
                 member: th.THIRFieldIdentity, definitions: 'Definitions') -> MIRFieldInitializer:
    field = MIRField(MIRFieldId(member.owner, member.name), member.type)
    if view_leaf(member.type):
        return _view_initializer(expr, params, field)
    if native_container_type(member.type):
        return _container_initializer(expr, params, field)
    if optional_record_field(member.type) is not None:
        return _optional_initializer(expr, params, field, definitions)
    if record_type(unwrap_readonly(member.type)):
        return _record_initializer(expr, params, field, definitions)
    if not owned_leaf(member.type):
        require(expr, _scalar_initializer(expr, params, member.type) == member.type, "constructor field type")
        literal = expr.expr if isinstance(expr, th.THIRCoerce) else expr
        source = expr.name if isinstance(expr, th.THIRName) else MIRConstant(literal.value)
        return MIRFieldInitializer(field, source, MIRMemberInitMode.SCALAR, False, expr.loc)
    # An owned leaf's member initializer copies or moves the parameter's
    # buffer, or materializes a constant; either allocation may raise.
    may_raise = bool(type_def_of(member.type).copy_may_raise)
    mode = MIRMemberInitMode.COPY
    match expr:
        case th.THIRName():
            plain(expr, {"name", "is_last_use", "is_movable"})
            name = expr
        case th.THIRMove():
            plain(expr, {"value"})
            name = expr.value
            require(expr, isinstance(name, th.THIRName), "constructor move needs parameter")
            plain(name, {"name", "is_last_use", "is_movable"})
            mode, may_raise = MIRMemberInitMode.MOVE, False
        case th.THIRFormConvert():
            # A view parameter materialized into the field's owned buffer.
            plain(expr, {"value", "is_const"})
            name = expr.value
            require(expr, expr.form is th.Form.STORAGE and isinstance(name, th.THIRName)
                    and name.form is th.Form.BORROW, "constructor form conversion")
            plain(name, {"name", "is_last_use", "is_movable"})
        case th.THIRStrLiteral() | th.THIRBytesLiteral() | th.THIRLiteral():
            plain(expr, {"value", "int_cpp"} if isinstance(expr, th.THIRLiteral) else {"value"})
            require(expr, expr.result_type == member.type and owned_constant(member.type, expr.value),
                    "constructor literal value")
            return MIRFieldInitializer(field, MIRConstant(expr.value), mode, may_raise, expr.loc)
        case _:
            raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")
    param = params.get(name.name)
    require(expr, param is not None and owned_parameter(param)
            and owned_value_type(param.type) == member.type == expr.result_type,
            "constructor initializer needs parameter")
    # Only the callee's own copy can be moved from.
    require(expr, mode is not MIRMemberInitMode.MOVE or param.passing in OWNING_PASSINGS,
            "constructor move needs an owned parameter")
    return MIRFieldInitializer(field, param.name, mode, may_raise, expr.loc)


@dataclass(frozen=True)
class MIRConstructorDefinition:
    constructor: th.THIRConstructor
    layout: MIRRecordLayout
    # One per layout field, in layout order.
    initializers: tuple[MIRFieldInitializer, ...]


# Verified definitions, or why a record has none, by record type: every one
# a record's definition reads -- its struct bases', its member records' and
# its container elements' -- at least.
Definitions = Mapping[NominalType, 'MIRConstructorDefinition | str']

_NO_DEFINITIONS: Definitions = MappingProxyType({})


@dataclass(frozen=True)
class _BaseLeg:
    """One base-constructor argument, as the base's initializers see its
    parameter: a child parameter lent or passed as the same scalar, a child
    parameter moved into the base's own copy, or a constant."""
    source: str | MIRConstant
    move: bool
    # The base parameter owns what it receives (its storage is built or
    # moved for the call), so an argument nothing consumes is an effect.
    owning: bool
    loc: SourceLocation | None
    # A literal the base parameter views in place (a view parameter, or one
    # passed as a view), so a member borrowing it stores static storage; a
    # literal bound to a reference parameter is a temporary of the call.
    static: bool = False


def _base_leg(arg: th.THIRExpr, target: th.THIRParam, params: Mapping[str, th.THIRParam],
              mismatch: str = "base argument needs matching parameter or literal") -> _BaseLeg:
    """A base-init (or member constructor) argument renders bare (`Base(name)`,
    with no parameter in view: `BUGS.md#base-init-args-separate-lowering`),
    so it binds the parameter exactly as written: a scalar parameter of its
    type, an owned-leaf parameter at the parameter's own borrowing passing (a
    lend, no buffer operation), a move of an owning parameter into an owning
    parameter, or a literal. A bare name into an owning parameter is a copy
    the leg does not model, and a record argument a lend or move it does not
    model; either refuses with `mismatch`."""
    owning = target.passing in OWNING_PASSINGS
    if view_parameter(target):
        # A view parameter takes a loan: one the child's lent parameter
        # holds, or a literal's static storage.
        source = _view_loan_source(arg, params, target.type, conversion=mismatch, lent=mismatch, literal=mismatch)
        return _BaseLeg(source, False, False, arg.loc, static=isinstance(source, MIRConstant))
    if scalar_param(target):
        match arg:
            case th.THIRName():
                plain(arg, {"name", "is_last_use", "is_movable"})
                child = params.get(arg.name)
                require(arg, child is not None and scalar_param(child) and child.type == target.type
                        and arg.result_type == target.type, mismatch)
                return _BaseLeg(child.name, False, False, arg.loc)
            case th.THIRLiteral() | th.THIRCoerce():
                require(arg, _scalar_initializer(arg, {}, target.type) == target.type, mismatch)
                literal = arg.expr if isinstance(arg, th.THIRCoerce) else arg
                return _BaseLeg(MIRConstant(literal.value), False, False, arg.loc)
        raise MIRUnsupported(arg, mismatch)
    require(arg, owned_parameter(target), mismatch)
    owned = owned_value_type(target.type)
    match arg:
        case th.THIRName():
            plain(arg, {"name", "is_last_use", "is_movable"})
            child = params.get(arg.name)
            require(arg, child is not None and owned_parameter(child) and owned_value_type(child.type) == owned
                    and target.passing in BORROWING_PASSINGS and child.passing is target.passing, mismatch)
            return _BaseLeg(child.name, False, False, arg.loc)
        case th.THIRMove():
            plain(arg, {"value"})
            name = arg.value
            require(arg, isinstance(name, th.THIRName), mismatch)
            plain(name, {"name", "is_last_use", "is_movable"})
            child = params.get(name.name)
            require(arg, child is not None and owned_parameter(child) and owned_value_type(child.type) == owned
                    and owning and child.passing in OWNING_PASSINGS, mismatch)
            return _BaseLeg(child.name, True, True, arg.loc)
        case th.THIRStrLiteral() | th.THIRBytesLiteral() | th.THIRLiteral():
            plain(arg, {"value", "int_cpp"} if isinstance(arg, th.THIRLiteral) else {"value"})
            require(arg, arg.result_type == owned and owned_constant(owned, arg.value), mismatch)
            return _BaseLeg(MIRConstant(arg.value), False, owning, arg.loc,
                            static=target.passing is ParamPassing.VIEW and isinstance(arg, th.THIRStrLiteral))
    raise MIRUnsupported(arg, mismatch)


def _compose(init: MIRFieldInitializer, legs: Mapping[str, _BaseLeg], role: str = "base") -> MIRFieldInitializer:
    """A base (or member) initializer seen from the constructor passing the
    arguments: its parameter source replaced by the leg that fed it. A lend
    or scalar keeps the initializer's mode; a move hands the member the
    child's storage (moved on, or copied from the callee's own copy, which
    may raise); a constant is materialized (an owned leaf's by a copy, which
    may raise); a composed member composes each of its initializers."""
    effect = f"{role} argument effect not modeled"
    if init.mode is MIRMemberInitMode.BORROW:
        # The member stores the loan the leg lends: the child's lent
        # parameter, the initializer a direct member init of it would be, or
        # a literal's static storage. A moved or owning leg dies with the call.
        if isinstance(init.source, MIRConstant):
            return replace(init, loc=None)
        leg = legs[init.source]
        require(init.field, not leg.move and not leg.owning
                and (isinstance(leg.source, str) or leg.static), f"{role} argument borrow not modeled")
        return replace(init, source=leg.source, loc=leg.loc)
    # A member the callee builds on its own has no line in the caller's body.
    match init.source:
        case MIRConstant() | MIROptionalConstruct():
            return replace(init, loc=None)
        case tuple():
            # A container literal over the callee's parameters copies each
            # element from what the leg lends.
            require(init.field, all(isinstance(legs[n].source, str) and not legs[n].move for n in init.source),
                    effect)
            return replace(init, source=tuple(legs[n].source for n in init.source), loc=None)
        case MIRComposedConstruct():
            children = tuple(_compose(child, legs, role) for child in init.source.initializers)
            return replace(init, source=MIRComposedConstruct(children),
                           may_raise=any(child.may_raise for child in children), loc=None)
    leg = legs[init.source]
    if isinstance(leg.source, MIRConstant):
        if init.mode is MIRMemberInitMode.SCALAR:
            return replace(init, source=leg.source, loc=leg.loc)
        require(init.field, owned_leaf(init.field.type), effect)
        return MIRFieldInitializer(init.field, leg.source, MIRMemberInitMode.COPY,
                                   bool(type_def_of(init.field.type).copy_may_raise), leg.loc)
    if leg.move:
        return MIRFieldInitializer(init.field, leg.source, MIRMemberInitMode.MOVE,
                                   init.mode is not MIRMemberInitMode.MOVE, leg.loc)
    return replace(init, source=leg.source, loc=leg.loc)


def _composed(node: object, definition: 'MIRConstructorDefinition', args: tuple[th.THIRExpr, ...],
              params: Mapping[str, th.THIRParam], role: str) -> list[MIRFieldInitializer]:
    """`definition`'s initializers seen from a constructor passing it `args`
    (`_compose`, one leg per argument). An owning leg no initializer
    consumes builds or moves storage nothing models."""
    legs = {target.name: _base_leg(arg, target, params, f"{role} argument needs matching parameter or literal")
            for arg, target in zip(args, definition.constructor.params)}
    fed: set[str] = set()
    composed: list[MIRFieldInitializer] = []
    for init in definition.initializers:
        fed |= _parameter_sources(init.source)
        composed.append(_compose(init, legs, role))
    require(node, all(not leg.owning or name in fed for name, leg in legs.items()),
            f"{role} argument effect not modeled")
    return composed


def _base_initializers(ctor: th.THIRConstructor, layout: th.THIRRecordLayout,
                       params: Mapping[str, th.THIRParam], definitions: Definitions) -> list[MIRFieldInitializer]:
    """The initializers of every ancestor field: each base constructor's
    verified definition composed with its arguments. Every struct-base
    ancestor is built by exactly one base initializer (directly, or through
    the base it is an ancestor of)."""
    built: set[NominalType] = set()
    composed: list[MIRFieldInitializer] = []
    for bi in ctor.base_inits:
        plain(bi, {"base_cpp", "base", "args"})
        require(ctor, isinstance(bi.base, NominalType) and bi.base in layout.ancestors, "base constructor identity")
        base = definitions.get(bi.base, "missing constructor definition")
        if isinstance(base, str):
            raise MIRUnsupported(ctor, f"base definition: {base}")
        reached = {bi.base, *base.layout.ancestors}
        require(ctor, not reached & built, "base constructor called twice")
        built |= reached
        if len(bi.args) != len(base.constructor.params):
            # A skipped base is spelled `Base()`: value-initialized, its
            # constructor never runs, so only a base with no fields is built.
            require(ctor, not bi.args and not base.layout.fields, "base constructor not called")
            continue
        composed += _composed(ctor, base, bi.args, params, "base")
    require(ctor, built == set(layout.ancestors), "base constructor not called")
    return composed


def _member_definitions(ctor: th.THIRConstructor, layout: th.THIRRecordLayout, definitions: Definitions) -> None:
    """A record's storage is hook-free to build, copy, move and destroy only
    when each member's is: a member record's verified definition, and a
    container field's elements as a container holds them (`container_definition`)."""
    for f in layout.fields:
        bare = inline_member_type(f.type)
        if record_type(bare):
            member = definitions.get(bare, "missing constructor definition")
            if isinstance(member, str):
                raise MIRUnsupported(ctor, f"member definition: {member}")
        elif native_container_type(bare):
            container_definition(ctor, bare, lambda t: definitions.get(t, "missing constructor definition"))


def _record_identity(node: object, layout: object) -> None:
    """A THIR record layout MIR models: a record identity, its distinct
    struct-base ancestors, and its eligibility facts."""
    require(node, isinstance(layout, th.THIRRecordLayout), "missing record layout")
    typ = layout.type
    require(node, record_type(typ), "unsupported record identity")
    require(node, isinstance(layout.ancestors, tuple) and len(set(layout.ancestors)) == len(layout.ancestors)
            and all(isinstance(a, NominalType) and record_type(a) and a != typ for a in layout.ancestors),
            "unsupported record identity")
    require(node, all(type(v) is bool for v in (
        layout.unique_constructor, layout.custom_copy, layout.custom_move,
        layout.custom_destructor, layout.copyable, layout.movable)), "invalid record eligibility")


def _stored_loan_member(typ: TpyType) -> bool:
    """A member whose loans the record object stores at member keys: a view
    member, or an inline record member (its own view members, keyed under
    the member's place; its definition decides its fields)."""
    return view_leaf(typ) or record_type(unwrap_readonly(typ))


def _record_members(node: object, layout: th.THIRRecordLayout) -> None:
    """Every field a modeled shape (`modeled_field`) keyed by an owner in
    the hierarchy. A view member stores its source's loan in the record
    object, and an inline record member's view members store theirs under
    the member's place; a container or wrapper member holding one would key
    it under an element or a payload, which no place models."""
    owners = (layout.type, *layout.ancestors)
    members = {MIRFieldId(f.owner, f.name) for f in layout.fields}
    require(node, not any(holds_loan(f.type) and not _stored_loan_member(f.type) for f in layout.fields),
            "record member holds a borrow")
    # `holds_loan` reads UNKNOWN as no loan; beside a stored loan that
    # unproved member (a native, protocol or `bytearray` field) could hold one too.
    require(node, not any(holds_loan(f.type) for f in layout.fields) or all(
        _stored_loan_member(f.type) or loan_free(f.type)
        for f in layout.fields), "record member loan unknown beside a view member")
    require(node, len(members) == len(layout.fields) and all(
        f.owner in owners and bool(f.name) and modeled_field(f.type)
        for f in layout.fields), "unsupported record fields")


def held_record_layout(source: th.THIRConstructor | th.THIRInheritedConstructor) -> MIRRecordLayout:
    """The layout a holder of the record reaches, decided from its fields
    alone: no construct, copy or destruction is admitted through it, so
    neither its initialization nor its special members matter."""
    layout = source.record_layout
    _record_identity(source, layout)
    _record_members(source, layout)
    return MIRRecordLayout(layout.type, tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields),
                           layout.copyable, layout.movable, ancestors=layout.ancestors)


def constructor_initialization(ctor: th.THIRConstructor,
                               definitions: Definitions = _NO_DEFINITIONS) -> MIRConstructorDefinition:
    """The verified initialization of a constructor: its member initializers
    and, for a derived record, each base constructor's definition (from
    `definitions`) composed with its arguments, all in layout order. Every
    member record and container element has a verified definition there."""
    plain(ctor, {"record_name", "params", "mil_inits", "base_inits", "body", "record_layout", "temp_plan",
                 "storage_facts"})
    layout = ctor.record_layout
    _record_identity(ctor, layout)
    typ = layout.type
    require(ctor, layout.unique_constructor, "constructor must be unique")
    require(ctor, not (layout.custom_copy or layout.custom_move or layout.custom_destructor),
            "custom record special member")
    members = {MIRFieldId(f.owner, f.name): f for f in layout.fields}
    _record_members(ctor, layout)
    _member_definitions(ctor, layout, definitions)
    params = {p.name: p for p in ctor.params}
    require(ctor, len(params) == len(ctor.params), "duplicate constructor parameter")
    for p in ctor.params:
        plain(p, {"name", "type", "passing", "native_container", "borrowed_record"}
              | ({"optional_layout"} if optional_parameter(p) is not None else set()))
        require(p, p.passing is not None, "unpublished parameter passing")
        require(p, scalar_param(p) or owned_parameter(p) or p.native_container is not None or record_parameter(p)
                or view_parameter(p) or optional_parameter(p) is not None, "constructor parameter type")
    initializers: dict[MIRFieldId, MIRFieldInitializer] = {}
    for mil in ctor.mil_inits:
        plain(mil, {"field_cpp", "field_identity", "value"})
        fact = mil.field_identity
        # The member-init list builds only the record's own fields; the
        # ancestors' are its bases'.
        require(ctor, isinstance(fact, th.THIRFieldIdentity) and fact.owner == typ
                and members.get(MIRFieldId(fact.owner, fact.name)) == fact, "constructor field identity")
        key = MIRFieldId(fact.owner, fact.name)
        require(ctor, key not in initializers, "duplicate constructor field")
        initializers[key] = _initializer(mil.value, params, fact, definitions)
    for init in _base_initializers(ctor, layout, params, definitions):
        require(ctor, init.field.id not in initializers, "duplicate constructor field")
        initializers[init.field.id] = init
    fields = tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields)
    require(ctor, set(initializers) == set(members), "incomplete constructor initialization")
    require(ctor, all(initializers[f.id].field == f for f in fields), "constructor field identity")
    return MIRConstructorDefinition(ctor, MIRRecordLayout(
        typ, fields, layout.copyable, layout.movable, ancestors=layout.ancestors),
        tuple(initializers[f.id] for f in fields))


def _caller_operands(ctor: th.THIRConstructor, initializers: tuple[MIRFieldInitializer, ...],
                     params: Mapping[str, th.THIRParam]) -> None:
    """Each member a caller's construct fills from its own operand: an
    argument the member copies through what it lends, or the temporary it
    hands over that the member moves from; a composed member the caller
    builds from the same operands."""
    for init in initializers:
        # A container member's initializer is the constructor body's own;
        # a caller's construct refuses it (`constructor container field`). A
        # view member takes the loan its operand holds, a literal's static
        # storage included.
        if (init.mode in (MIRMemberInitMode.SCALAR, MIRMemberInitMode.BORROW)
                or native_container_type(init.field.type)):
            continue
        if isinstance(init.source, MIRComposedConstruct):
            _caller_operands(ctor, init.source.initializers, params)
            continue
        # A caller's construct has no operand for a constant, nor one both
        # copied and moved.
        require(ctor, isinstance(init.source, str), "constructor owned-leaf constant")
        # An Optional record parameter lends its payload through a pointer.
        require(ctor, init.mode is MIRMemberInitMode.MOVE or params[init.source].passing in BORROWING_PASSINGS
                or optional_parameter(params[init.source]) is not None, "constructor copies an owned parameter")
        # A move into a base's copy: the caller's operand is moved, and the
        # base copies its own copy.
        require(ctor, not (init.mode is MIRMemberInitMode.MOVE and init.may_raise),
                "constructor copies an owned parameter")


def _verify(ctor: th.THIRConstructor, definitions: Definitions = _NO_DEFINITIONS) -> MIRConstructorDefinition:
    """A definition a CALLER may construct through: pure initialization from
    its arguments. A caller's construct lends an owned-leaf or record
    argument the member copies, or hands over the temporary a member moves
    from."""
    definition = constructor_initialization(ctor, definitions)
    for param in ctor.params:
        require(param, scalar_param(param) or owned_parameter(param) or record_parameter(param)
                or view_parameter(param) or optional_parameter(param) is not None, "constructor parameter type")
    _caller_operands(ctor, definition.initializers, {p.name: p for p in ctor.params})
    for stmt in ctor.body:
        require(stmt, isinstance(stmt, th.THIRNoOpStmt), "constructor body effects")
        plain(stmt, set())
    return definition


def _inherited(inherited: th.THIRInheritedConstructor, base: MIRConstructorDefinition) -> MIRConstructorDefinition:
    """A record constructing through its one struct base's constructor
    (`using Base::Base;`): the base's definition at the record's type. It
    must add nothing the base's constructor does not build -- no own field,
    no other base, no own special member."""
    plain(inherited, {"record_layout", "base"})
    layout = inherited.record_layout
    require(inherited, isinstance(layout, th.THIRRecordLayout) and record_type(layout.type),
            "missing record layout")
    fields = tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields)
    require(inherited, layout.ancestors == (inherited.base, *base.layout.ancestors)
            and fields == base.layout.fields
            and not (layout.custom_copy or layout.custom_move or layout.custom_destructor)
            and layout.copyable is base.layout.copyable and layout.movable is base.layout.movable,
            "inherited constructor shape")
    # The base's initializers are reused whole: the record's parameters are
    # the base's, so a stored loan is the one the base's parameter lends.
    return MIRConstructorDefinition(base.constructor, MIRRecordLayout(
        layout.type, fields, layout.copyable, layout.movable, ancestors=layout.ancestors), base.initializers)


@dataclass(frozen=True)
class MIRContainerDefinition:
    """The builtin certificate of a native container's storage, from its
    TypeDef and type arguments: an opaque buffer whose members MIR types
    but never tracks one by one. `layout` carries mutable members; a holder
    applies its own access (`with_access`). `records` are the definitions
    of its record members, which the body's layouts must carry."""
    layout: MIRContainerLayout
    records: tuple['MIRConstructorDefinition', ...] = ()


def with_access(layout: MIRContainerLayout, readonly: bool) -> MIRContainerLayout:
    """A holder's layout: a readonly holder reaches readonly storage members
    (an inert leaf is read by value and carries no access)."""
    def member(m: MIRTupleElement | None) -> MIRTupleElement | None:
        if m is None or m.kind is MIRValueKind.SCALAR:
            return m
        return MIRTupleElement(m.type, m.kind, m.readonly or readonly)
    return MIRContainerLayout(member(layout.element), member(layout.value))


@dataclass(frozen=True)
class MIROwnedLeafDefinition:
    """The builtin certificate of an owned leaf's storage (`TypeDef.owned_leaf`):
    one opaque buffer, copied, moved and destroyed with no hook. Kept apart
    from verified constructor definitions; nothing else earns it."""
    layout: MIRRecordLayout


@dataclass(frozen=True)
class MIRHeldLayout:
    """The layout of a record a body reaches only through a holder
    (`held_record_layout`), whether or not its definition verifies. Never a
    construct's, copy's or destruction's definition."""
    layout: MIRRecordLayout


@dataclass(frozen=True, init=False)
class MIRDefinitions:
    """Index and check each actual emitted definition once, including failures."""
    records: Mapping[NominalType, MIRConstructorDefinition | str]
    # The layout of every record whose fields MIR models (`held_record_layout`),
    # a record with no verified definition included.
    layouts: Mapping[NominalType, MIRRecordLayout]

    def __init__(self, constructors: tuple[th.THIRConstructor, ...] = (), *,
                 inherited: tuple[th.THIRInheritedConstructor, ...] = ()) -> None:
        sources: dict[NominalType, th.THIRConstructor | th.THIRInheritedConstructor | str] = {}
        for source in (*constructors, *inherited):
            if source.record_layout is None:
                continue
            typ = source.record_layout.type
            sources[typ] = "duplicate constructor definition" if typ in sources else source
        records: dict[NominalType, MIRConstructorDefinition | str] = {}
        layouts: dict[NominalType, MIRRecordLayout] = {}

        def verify(typ: NominalType) -> MIRConstructorDefinition | str:
            # Dependencies first: a definition composes its bases' and its
            # member records' verified ones and reads its container
            # elements'. Inheritance is acyclic and diamond-free (sema) and
            # C++ forbids a record holding itself inline, so the recursion
            # ends; the placeholder only guards malformed input.
            if typ in records:
                return records[typ]
            source = sources.get(typ)
            if source is None:
                return "missing constructor definition"
            if isinstance(source, str):
                records[typ] = source
                return source
            records[typ] = "cyclic record definition"
            try:
                layouts[typ] = held_record_layout(source)
            except MIRUnsupported:
                pass
            try:
                if isinstance(source, th.THIRInheritedConstructor):
                    base = verify(source.base) if isinstance(source.base, NominalType) else "missing base"
                    if isinstance(base, str):
                        raise MIRUnsupported(source, f"base definition: {base}")
                    records[typ] = _inherited(source, base)
                else:
                    for read in _read_records(source):
                        verify(read)
                    records[typ] = _verify(source, records)
            except MIRUnsupported as failure:
                records[typ] = failure.reason
            return records[typ]

        for typ in sources:
            verify(typ)
        object.__setattr__(self, "records", MappingProxyType(records))
        object.__setattr__(self, "layouts", MappingProxyType(layouts))

    def get(self, node: object, typ: NominalType
            ) -> MIRConstructorDefinition | MIROwnedLeafDefinition | MIRContainerDefinition:
        if owned_leaf(typ):
            return MIROwnedLeafDefinition(MIRRecordLayout(typ, (), True, True, opaque=True))
        if native_container_type(typ) or container_view(typ):
            return self.container(node, typ)
        definition = self.records.get(typ, "missing constructor definition")
        if isinstance(definition, str):
            raise MIRUnsupported(node, definition)
        return definition

    def held_layout(self, node: object, typ: NominalType) -> MIRHeldLayout:
        """The layout a holder of record `typ` reaches (`layouts`), or the
        refusal of its definition."""
        layout = self.layouts.get(typ)
        if layout is None:
            definition = self.records.get(typ, "missing constructor definition")
            raise MIRUnsupported(node, definition if isinstance(definition, str) else "missing record layout")
        return MIRHeldLayout(layout)

    def container(self, node: object, typ: NominalType) -> MIRContainerDefinition:
        """A native container's or container view's layout, or the refusal
        of its members (`container_definition`)."""
        return container_definition(node, typ, lambda t: self.records.get(t, "missing constructor definition"))


def _read_records(ctor: th.THIRConstructor) -> list[NominalType]:
    """The records whose definitions `ctor`'s definition reads: its struct
    bases, its record fields, and its container fields' record members."""
    reads = [bi.base for bi in ctor.base_inits if isinstance(bi.base, NominalType)]
    for f in ctor.record_layout.fields:
        bare = inline_member_type(f.type)
        members = declared_members(bare) if native_container_type(bare) else None
        for typ in (bare,) if members is None else members[:2]:
            if typ is not None and record_type(unwrap_readonly(typ)):
                reads.append(unwrap_readonly(typ))
    return reads


def container_definition(node: object, typ: NominalType,
                         definition_of: Callable[[NominalType], MIRConstructorDefinition | str]
                         ) -> MIRContainerDefinition:
    """A native container's or container view's layout: the members its
    stub declares (`declared_members`; a view's own, at its own type
    arguments), a record member read through its verified definition
    (`definition_of`), or the refusal of its members."""
    members = declared_members(typ)
    require(node, members is not None, "unsupported native container type")
    element, value, _ = members
    records: dict[NominalType, MIRConstructorDefinition] = {}
    # A record is admitted only as the single element of a type; the two
    # members of a type with a value member are both leaves.
    leaves_only = value is not None
    layout = tuple(None if m is None else _container_member(node, m, leaves_only, records, definition_of)
                   for m in (element, value))
    return MIRContainerDefinition(MIRContainerLayout(*layout), tuple(records.values()))


def _container_member(node: object, typ: TpyType, leaves_only: bool,
                      records: dict[NominalType, MIRConstructorDefinition],
                      definition_of: Callable[[NominalType], MIRConstructorDefinition | str]) -> MIRTupleElement:
    """One container member: an inert leaf, an owned leaf, or (in a type
    with no value member) a plain record whose fields are leaves."""
    # A stored borrow outlives the operation that put it there, which
    # needs the retention contracts MIR does not model yet.
    require(node, not holds_loan(typ), "container holds a borrow")
    readonly = isinstance(typ, ReadonlyType)
    bare = unwrap_readonly(typ)
    if storage_leaf(bare):
        return MIRTupleElement(bare)
    if owned_leaf(bare):
        return MIRTupleElement(bare, MIRValueKind.OWNED, readonly)
    # A nested container or a record holding one has element places
    # beyond one hop.
    require(node, not leaves_only and isinstance(bare, NominalType) and plain_record_element(bare),
            "unsupported native container element")
    definition = definition_of(bare)
    if isinstance(definition, str):
        raise MIRUnsupported(node, definition)
    require(node, all(storage_leaf(f.type) or owned_leaf(f.type) for f in definition.layout.fields),
            "unsupported native container element")
    records[bare] = definition
    return MIRTupleElement(bare, MIRValueKind.BORROWED, readonly)
