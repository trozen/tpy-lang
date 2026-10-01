"""Immutable, verified constructor definitions supplied by the THIR caller,
and the builtin certificates of owned leaves' opaque storage."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..thir import nodes as th
from ..thir.scalar_leaves import leaf_constant, owned_leaf, record_type, storage_leaf
from ..typesys import NominalType, TpyType
from .coverage import MIRUnsupported, literal_type, plain, require, scalar_param
from .nodes import MIRField, MIRFieldId, MIRRecordLayout


def _initializer(expr: th.THIRExpr, params: dict[str, TpyType], expected: TpyType) -> TpyType:
    require(expr, expr.form is th.Form.VALUE, "constructor initializer form")
    typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
    require(expr, storage_leaf(typ), "constructor initializer type")
    match expr:
        case th.THIRName():
            plain(expr, {"name", "is_last_use", "is_movable"})
            require(expr, params.get(expr.name) == typ, "constructor initializer needs parameter")
        case th.THIRLiteral():
            plain(expr, {"value", "int_cpp"})
            require(expr, leaf_constant(typ, expr.value), "constructor literal value")
        case th.THIRCoerce():
            plain(expr, {"expr", "coercion_name"})
            require(expr, expr.coercion_name == "int_literal_to_fixed_int"
                    and isinstance(expr.expr, th.THIRLiteral), "constructor coercion")
            require(expr, _initializer(expr.expr, params, typ) == typ, "constructor coercion type")
        case _:
            raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")
    return typ


@dataclass(frozen=True)
class MIRConstructorDefinition:
    constructor: th.THIRConstructor
    layout: MIRRecordLayout


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
    require(ctor, len(members) == len(layout.fields) and all(
        f.owner == typ and bool(f.name) and storage_leaf(f.type) for f in layout.fields),
        "unsupported record fields")
    params = {p.name: p.type for p in ctor.params}
    require(ctor, len(params) == len(ctor.params), "duplicate constructor parameter")
    for p in ctor.params:
        plain(p, {"name", "type", "passing", "native_container"})
        require(p, p.passing is not None, "unpublished parameter passing")
        require(p, scalar_param(p) or p.native_container is not None, "constructor parameter type")
    initialized: set[str] = set()
    for mil in ctor.mil_inits:
        plain(mil, {"field_cpp", "field_identity", "value"})
        fact = mil.field_identity
        require(ctor, isinstance(fact, th.THIRFieldIdentity)
                and members.get(fact.name) == fact, "constructor field identity")
        require(ctor, fact.name not in initialized, "duplicate constructor field")
        require(mil.value, _initializer(mil.value, params, fact.type) == fact.type, "constructor field type")
        initialized.add(fact.name)
    require(ctor, initialized == set(members), "incomplete constructor initialization")
    return MIRConstructorDefinition(ctor, MIRRecordLayout(
        typ, tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields),
        layout.copyable, layout.movable))


def _verify(ctor: th.THIRConstructor) -> MIRConstructorDefinition:
    definition = constructor_initialization(ctor)
    for param in ctor.params:
        require(param, scalar_param(param), "constructor parameter type")
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
