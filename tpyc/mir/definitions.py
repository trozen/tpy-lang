"""Immutable, verified constructor definitions supplied by the THIR caller,
and the builtin certificates of owned leaves' opaque storage."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.scalar_leaves import (
    leaf_constant, owned_constant, owned_leaf, owned_value_type, record_type, storage_leaf,
)
from ..type_def_registry import type_def_of
from ..typesys import Loan, NominalType, TpyType, loan_class
from .call_contract import BORROWING_PASSINGS, OWNING_PASSINGS
from .coverage import MIRUnsupported, literal_type, plain, require, scalar_param
from .nodes import MIRConstant, MIRField, MIRFieldId, MIRMemberInitMode, MIRRecordLayout


@dataclass(frozen=True)
class MIRFieldInitializer:
    """How the member initializer list fills one field: from a constructor
    parameter (named) or a constant, by value (an inert leaf), by copy or by
    move (an owned leaf). Decided once here; the constructor body and every
    caller read it."""
    field: MIRField
    source: str | MIRConstant
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


def _initializer(expr: th.THIRExpr, params: Mapping[str, th.THIRParam],
                 member: th.THIRFieldIdentity) -> MIRFieldInitializer:
    field = MIRField(MIRFieldId(member.owner, member.name), member.type)
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
        f.owner == typ and bool(f.name) and (storage_leaf(f.type) or owned_leaf(f.type)) for f in layout.fields),
        "unsupported record fields")
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
        if init.mode is MIRMemberInitMode.SCALAR:
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

    def get(self, node: object, typ: NominalType) -> MIRConstructorDefinition | MIROwnedLeafDefinition:
        if owned_leaf(typ):
            return MIROwnedLeafDefinition(MIRRecordLayout(typ, (), True, True, opaque=True))
        definition = self.records.get(typ, "missing constructor definition")
        if isinstance(definition, str):
            raise MIRUnsupported(node, definition)
        return definition
