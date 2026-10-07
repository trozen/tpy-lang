"""Immutable, verified constructor definitions supplied by the THIR caller,
and the builtin certificates of owned leaves' opaque storage."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.scalar_leaves import (
    container_view, declared_members, leaf_constant, modeled_field, native_container_type, owned_constant,
    owned_leaf, owned_value_type, plain_record_element, record_type, storage_leaf,
)
from ..type_def_registry import ParamPassing, type_def_of
from ..typesys import (
    Loan, NominalType, OwnType, ReadonlyType, TpyType, loan_class, unwrap_readonly, unwrap_ref_type,
)
from .call_contract import BORROWING_PASSINGS, OWNING_PASSINGS
from .coverage import MIRUnsupported, literal_type, plain, require, scalar_param
from .nodes import (
    MIRConstant, MIRContainerLayout, MIRField, MIRFieldId, MIRMemberInitMode, MIRRecordLayout, MIRTupleElement,
    MIRValueKind,
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
    # a record field a construct of its own constructor.
    source: str | MIRConstant | tuple[str, ...] | MIRComposedConstruct
    mode: MIRMemberInitMode
    # The copy (or a constant's materialization) can exit by exception.
    may_raise: bool
    loc: SourceLocation | None = None


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
        bare = unwrap_readonly(f.type)
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
            plain(expr, {"type_cpp", "args"})
            require(expr, expr.form is th.Form.STORAGE and unwrap_readonly(expr.result_type) == typ,
                    "constructor field type")
            require(expr, len(expr.args) == len(definition.constructor.params),
                    "member argument needs matching parameter or literal")
            initializers = _composed(expr, definition, expr.args, params, "member")
            return MIRFieldInitializer(field, MIRComposedConstruct(tuple(initializers)), MIRMemberInitMode.MOVE,
                                       any(init.may_raise for init in initializers), expr.loc)
    raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")


def _initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam],
                 member: th.THIRFieldIdentity, definitions: 'Definitions') -> MIRFieldInitializer:
    field = MIRField(MIRFieldId(member.owner, member.name), member.type)
    if native_container_type(member.type):
        return _container_initializer(expr, params, field)
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
            return _BaseLeg(MIRConstant(arg.value), False, owning, arg.loc)
    raise MIRUnsupported(arg, mismatch)


def _compose(init: MIRFieldInitializer, legs: Mapping[str, _BaseLeg], role: str = "base") -> MIRFieldInitializer:
    """A base (or member) initializer seen from the constructor passing the
    arguments: its parameter source replaced by the leg that fed it. A lend
    or scalar keeps the initializer's mode; a move hands the member the
    child's storage (moved on, or copied from the callee's own copy, which
    may raise); a constant is materialized (an owned leaf's by a copy, which
    may raise); a composed member composes each of its initializers."""
    effect = f"{role} argument effect not modeled"
    # A member the callee builds on its own has no line in the caller's body.
    match init.source:
        case MIRConstant():
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
        bare = unwrap_readonly(f.type)
        if record_type(bare):
            member = definitions.get(bare, "missing constructor definition")
            if isinstance(member, str):
                raise MIRUnsupported(ctor, f"member definition: {member}")
        elif native_container_type(bare):
            container_definition(ctor, bare, lambda t: definitions.get(t, "missing constructor definition"))


def constructor_initialization(ctor: th.THIRConstructor,
                               definitions: Definitions = _NO_DEFINITIONS) -> MIRConstructorDefinition:
    """The verified initialization of a constructor: its member initializers
    and, for a derived record, each base constructor's definition (from
    `definitions`) composed with its arguments, all in layout order. Every
    member record and container element has a verified definition there."""
    plain(ctor, {"record_name", "params", "mil_inits", "base_inits", "body", "record_layout", "temp_plan",
                 "storage_facts"})
    layout = ctor.record_layout
    require(ctor, isinstance(layout, th.THIRRecordLayout), "missing record layout")
    typ = layout.type
    require(ctor, record_type(typ), "unsupported record identity")
    require(ctor, isinstance(layout.ancestors, tuple) and len(set(layout.ancestors)) == len(layout.ancestors)
            and all(isinstance(a, NominalType) and record_type(a) and a != typ for a in layout.ancestors),
            "unsupported record identity")
    require(ctor, all(type(v) is bool for v in (
        layout.unique_constructor, layout.custom_copy, layout.custom_move,
        layout.custom_destructor, layout.copyable, layout.movable)), "invalid record eligibility")
    require(ctor, layout.unique_constructor, "constructor must be unique")
    require(ctor, not (layout.custom_copy or layout.custom_move or layout.custom_destructor),
            "custom record special member")
    owners = (typ, *layout.ancestors)
    members = {MIRFieldId(f.owner, f.name): f for f in layout.fields}
    # A stored view retains its source's loan past the constructor, which
    # needs the call retention contracts MIR does not model yet.
    require(ctor, not any(loan_class(f.type).holds is Loan.YES for f in layout.fields), "record holds a borrow")
    require(ctor, len(members) == len(layout.fields) and all(
        f.owner in owners and bool(f.name) and modeled_field(f.type)
        for f in layout.fields), "unsupported record fields")
    _member_definitions(ctor, layout, definitions)
    params = {p.name: p for p in ctor.params}
    require(ctor, len(params) == len(ctor.params), "duplicate constructor parameter")
    for p in ctor.params:
        plain(p, {"name", "type", "passing", "native_container", "borrowed_record"})
        require(p, p.passing is not None, "unpublished parameter passing")
        require(p, scalar_param(p) or owned_parameter(p) or p.native_container is not None or record_parameter(p),
                "constructor parameter type")
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
        # a caller's construct refuses it (`constructor container field`).
        if init.mode is MIRMemberInitMode.SCALAR or native_container_type(init.field.type):
            continue
        if isinstance(init.source, MIRComposedConstruct):
            _caller_operands(ctor, init.source.initializers, params)
            continue
        # A caller's construct has no operand for a constant, nor one both
        # copied and moved.
        require(ctor, isinstance(init.source, str), "constructor owned-leaf constant")
        require(ctor, init.mode is MIRMemberInitMode.MOVE or params[init.source].passing in BORROWING_PASSINGS,
                "constructor copies an owned parameter")
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
        require(param, scalar_param(param) or owned_parameter(param) or record_parameter(param),
                "constructor parameter type")
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


@dataclass(frozen=True, init=False)
class MIRDefinitions:
    """Index and check each actual emitted definition once, including failures."""
    records: Mapping[NominalType, MIRConstructorDefinition | str]

    def __init__(self, constructors: tuple[th.THIRConstructor, ...] = (), *,
                 inherited: tuple[th.THIRInheritedConstructor, ...] = ()) -> None:
        sources: dict[NominalType, th.THIRConstructor | th.THIRInheritedConstructor | str] = {}
        for source in (*constructors, *inherited):
            if source.record_layout is None:
                continue
            typ = source.record_layout.type
            sources[typ] = "duplicate constructor definition" if typ in sources else source
        records: dict[NominalType, MIRConstructorDefinition | str] = {}

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

    def container(self, node: object, typ: NominalType) -> MIRContainerDefinition:
        """A native container's or container view's layout, or the refusal
        of its members (`container_definition`)."""
        return container_definition(node, typ, lambda t: self.records.get(t, "missing constructor definition"))


def _read_records(ctor: th.THIRConstructor) -> list[NominalType]:
    """The records whose definitions `ctor`'s definition reads: its struct
    bases, its record fields, and its container fields' record members."""
    reads = [bi.base for bi in ctor.base_inits if isinstance(bi.base, NominalType)]
    for f in ctor.record_layout.fields:
        bare = unwrap_readonly(f.type)
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
    require(node, loan_class(typ).holds is not Loan.YES, "container holds a borrow")
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
