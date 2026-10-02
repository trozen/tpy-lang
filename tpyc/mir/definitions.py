"""Immutable, verified constructor definitions supplied by the THIR caller,
and the builtin certificates of owned leaves' opaque storage."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.scalar_leaves import (
    container_members, container_view, leaf_constant, native_container_type, owned_constant, owned_leaf,
    owned_value_type, plain_record_element, record_type, storage_leaf, view_iteration_index,
)
from ..type_def_registry import type_def_of
from ..typesys import (
    Loan, NominalType, ReadonlyType, TpyType, loan_class, unwrap_readonly,
)
from .call_contract import BORROWING_PASSINGS, OWNING_PASSINGS
from .coverage import MIRUnsupported, literal_type, plain, require, scalar_param
from .nodes import (
    MIRConstant, MIRContainerLayout, MIRField, MIRFieldId, MIRMemberInitMode, MIRRecordLayout, MIRTupleElement,
    MIRValueKind,
)

@dataclass(frozen=True)
class MIRFieldInitializer:
    """How the member initializer list fills one field: from a constructor
    parameter (named) or a constant, by value (an inert leaf), by copy or by
    move (an owned leaf). Decided once here; the constructor body and every
    caller read it."""
    field: MIRField
    # A container field may also be a literal over parameters, one name per
    # element (a dict's keys and values alternating), moved in once built.
    source: str | MIRConstant | tuple[str, ...]
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
            element, value, _ = container_members(typ)
            operands: list[str] = []
            for i, e in enumerate(expr.elements):
                operands.append(_literal_element(e, params, unwrap_readonly(element)))
                if value is not None:
                    operands.append(_literal_element(expr.values[i], params, unwrap_readonly(value)))
            require(expr, value is None or len(expr.values) == len(expr.elements), "constructor field type")
            return MIRFieldInitializer(field, tuple(operands), MIRMemberInitMode.MOVE, True, expr.loc)
    raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")


def _initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam],
                 member: th.THIRFieldIdentity) -> MIRFieldInitializer:
    field = MIRField(MIRFieldId(member.owner, member.name), member.type)
    if native_container_type(member.type):
        return _container_initializer(expr, params, field)
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


def constructor_initialization(ctor: th.THIRConstructor) -> MIRConstructorDefinition:
    plain(ctor, {"record_name", "params", "mil_inits", "body", "record_layout", "temp_plan", "storage_facts"})
    layout = ctor.record_layout
    require(ctor, isinstance(layout, th.THIRRecordLayout), "missing record layout")
    typ = layout.type
    require(ctor, record_type(typ), "unsupported record identity")
    require(ctor, all(type(v) is bool for v in (
        layout.unique_constructor, layout.custom_copy, layout.custom_move,
        layout.custom_destructor, layout.copyable, layout.movable)), "invalid record eligibility")
    require(ctor, layout.unique_constructor, "constructor must be unique")
    require(ctor, not (layout.custom_copy or layout.custom_move or layout.custom_destructor),
            "custom record special member")
    members = {f.name: f for f in layout.fields}
    # A stored view retains its source's loan past the constructor, which
    # needs the call retention contracts MIR does not model yet.
    require(ctor, not any(loan_class(f.type).holds is Loan.YES for f in layout.fields), "record holds a borrow")
    require(ctor, len(members) == len(layout.fields) and all(
        f.owner == typ and bool(f.name) and (storage_leaf(f.type) or owned_leaf(f.type) or native_container_type(f.type))
        for f in layout.fields), "unsupported record fields")
    params = {p.name: p for p in ctor.params}
    require(ctor, len(params) == len(ctor.params), "duplicate constructor parameter")
    for p in ctor.params:
        plain(p, {"name", "type", "passing", "native_container"})
        require(p, p.passing is not None, "unpublished parameter passing")
        require(p, scalar_param(p) or owned_parameter(p) or p.native_container is not None,
                "constructor parameter type")
    initializers: dict[str, MIRFieldInitializer] = {}
    for mil in ctor.mil_inits:
        plain(mil, {"field_cpp", "field_identity", "value"})
        fact = mil.field_identity
        require(ctor, isinstance(fact, th.THIRFieldIdentity)
                and members.get(fact.name) == fact, "constructor field identity")
        require(ctor, fact.name not in initializers, "duplicate constructor field")
        initializers[fact.name] = _initializer(mil.value, params, fact)
    require(ctor, set(initializers) == set(members), "incomplete constructor initialization")
    return MIRConstructorDefinition(ctor, MIRRecordLayout(
        typ, tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields),
        layout.copyable, layout.movable), tuple(initializers[f.name] for f in layout.fields))


def _verify(ctor: th.THIRConstructor) -> MIRConstructorDefinition:
    """A definition a CALLER may construct through: pure initialization from
    its arguments. A caller's construct lends an owned-leaf argument the
    member copies, or hands over the temporary a member moves from."""
    definition = constructor_initialization(ctor)
    params = {p.name: p for p in ctor.params}
    for param in ctor.params:
        require(param, scalar_param(param) or owned_parameter(param), "constructor parameter type")
    for init in definition.initializers:
        # A container member's initializer is the constructor body's own;
        # a caller's construct refuses it (`constructor container field`).
        if init.mode is MIRMemberInitMode.SCALAR or native_container_type(init.field.type):
            continue
        # A caller's construct has no operand for a constant, nor one both
        # copied and moved.
        require(ctor, isinstance(init.source, str), "constructor owned-leaf constant")
        require(ctor, init.mode is MIRMemberInitMode.MOVE or params[init.source].passing in BORROWING_PASSINGS,
                "constructor copies an owned parameter")
    for stmt in ctor.body:
        require(stmt, isinstance(stmt, th.THIRNoOpStmt), "constructor body effects")
        plain(stmt, set())
    return definition


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


def iteration_layout(typ: TpyType, layout: MIRContainerLayout) -> MIRContainerLayout | None:
    """What an iterator over a holder of `typ` with `layout` walks: the
    layout itself, or for a view of a dict's keys and values the member its
    iteration declares (`view_iteration_index`: a values view walks the
    values). None when the view yields no member (an items view's tuples)."""
    if not container_view(typ) or layout.value is None:
        return layout
    index = view_iteration_index(typ)
    member = None if index is None else (layout.element, layout.value)[index]
    return None if member is None else MIRContainerLayout(member)


def iterated_layout(typ: TpyType, layout: MIRContainerLayout,
                    element: TpyType | th.THIRBorrowedRecord) -> MIRContainerLayout:
    """`iteration_layout` for a loop whose THIR iteration fact names the
    member it yields (`THIRNativeIteration.source.element`): a view of a
    dict's keys and values walks the layout member of that type."""
    if not container_view(typ) or layout.value is None:
        return layout
    member_type = element.type if isinstance(element, th.THIRBorrowedRecord) else element
    member = next((m for m in (layout.element, layout.value) if m.type == member_type), None)
    require(element, member is not None, "native iteration source disagrees with binding")
    return MIRContainerLayout(member)


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

    def __init__(self, constructors: tuple[th.THIRConstructor, ...] = ()) -> None:
        records: dict[NominalType, MIRConstructorDefinition | str] = {}
        for ctor in constructors:
            if ctor.record_layout is None:
                continue
            typ = ctor.record_layout.type
            if typ in records:
                records[typ] = "duplicate constructor definition"
                continue
            try:
                records[typ] = _verify(ctor)
            except MIRUnsupported as failure:
                records[typ] = failure.reason
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
        """A native container's layout (a container view's: the region it views), or
        the refusal of its members."""
        members = container_members(typ)
        require(node, members is not None, "unsupported native container type")
        element, value, key = members
        records: dict[NominalType, MIRConstructorDefinition] = {}
        layout = tuple(None if m is None else self.member(node, m, hashed, records)
                        for m, hashed in ((element, key), (value, False)))
        return MIRContainerDefinition(MIRContainerLayout(*layout), tuple(records.values()))

    def member(self, node: object, typ: TpyType, hashed: bool,
               records: dict[NominalType, MIRConstructorDefinition]) -> MIRTupleElement:
        """One container member: an inert leaf, an owned leaf, or (never as
        a hashed key) a plain record whose fields are leaves."""
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
        require(node, not hashed and isinstance(bare, NominalType) and plain_record_element(bare),
                "unsupported native container element")
        definition = self.get(node, bare)
        require(node, isinstance(definition, MIRConstructorDefinition) and all(
            storage_leaf(f.type) or owned_leaf(f.type) for f in definition.layout.fields),
            "unsupported native container element")
        records[bare] = definition
        return MIRTupleElement(bare, MIRValueKind.BORROWED, readonly)
