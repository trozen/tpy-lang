"""Immutable, verified constructor definitions supplied by the THIR caller."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..thir import nodes as th
from ..typesys import BOOL, INT32, INT32_MAX, INT32_MIN, IntLiteralType, NominalType, TpyType
from .coverage import MIRUnsupported, plain, require
from .nodes import MIRField, MIRFieldId, MIRRecordLayout


def _initializer(expr: th.THIRExpr, params: dict[str, TpyType]) -> TpyType:
    require(expr, expr.form is th.Form.VALUE, "constructor initializer form")
    typ = INT32 if isinstance(expr.result_type, IntLiteralType) else expr.result_type
    require(expr, typ in (BOOL, INT32), "constructor initializer type")
    match expr:
        case th.THIRName():
            plain(expr, {"name", "is_last_use", "is_movable"})
            require(expr, params.get(expr.name) == typ, "constructor initializer needs parameter")
        case th.THIRLiteral():
            plain(expr, {"value", "int_cpp"})
            require(expr, (typ == BOOL and type(expr.value) is bool)
                    or (typ == INT32 and type(expr.value) is int
                        and INT32_MIN <= expr.value <= INT32_MAX), "constructor literal value")
        case th.THIRCoerce():
            plain(expr, {"expr", "coercion_name"})
            require(expr, typ == INT32 and expr.coercion_name == "int_literal_to_fixed_int"
                    and isinstance(expr.expr, th.THIRLiteral), "constructor coercion")
            require(expr, _initializer(expr.expr, params) == typ, "constructor coercion type")
        case _:
            raise MIRUnsupported(expr, "constructor initializer needs parameter or literal")
    return typ


@dataclass(frozen=True)
class MIRConstructorDefinition:
    constructor: th.THIRConstructor
    layout: MIRRecordLayout


def _verify(ctor: th.THIRConstructor) -> MIRConstructorDefinition:
    plain(ctor, {"record_name", "params", "mil_inits", "body", "record_layout"})
    layout = ctor.record_layout
    require(ctor, isinstance(layout, th.THIRRecordLayout), "missing record layout")
    typ = layout.type
    require(ctor, isinstance(typ, NominalType) and typ.qualified_name() is not None
            and typ not in (BOOL, INT32) and not typ.type_args and not typ.is_protocol,
            "unsupported record identity")
    require(ctor, all(type(v) is bool for v in (
        layout.unique_constructor, layout.custom_copy, layout.custom_move,
        layout.custom_destructor, layout.copyable, layout.movable)), "invalid record eligibility")
    require(ctor, layout.unique_constructor, "constructor must be unique")
    require(ctor, not (layout.custom_copy or layout.custom_move or layout.custom_destructor),
            "custom record special member")
    members = {f.name: f for f in layout.fields}
    require(ctor, len(members) == len(layout.fields) and all(
        f.owner == typ and bool(f.name) and f.type in (BOOL, INT32) for f in layout.fields),
        "unsupported record fields")
    params = {p.name: p.type for p in ctor.params}
    require(ctor, len(params) == len(ctor.params), "duplicate constructor parameter")
    for p in ctor.params:
        plain(p, {"name", "type"})
        require(p, p.type in (BOOL, INT32), "constructor parameter type")
    for stmt in ctor.body:
        require(stmt, isinstance(stmt, th.THIRNoOpStmt), "constructor body effects")
        plain(stmt, set())
    initialized: set[str] = set()
    for mil in ctor.mil_inits:
        plain(mil, {"field_cpp", "field_identity", "value"})
        fact = mil.field_identity
        require(ctor, isinstance(fact, th.THIRFieldIdentity)
                and members.get(fact.name) == fact, "constructor field identity")
        require(ctor, fact.name not in initialized, "duplicate constructor field")
        require(mil.value, _initializer(mil.value, params) == fact.type, "constructor field type")
        initialized.add(fact.name)
    require(ctor, initialized == set(members), "incomplete constructor initialization")
    return MIRConstructorDefinition(ctor, MIRRecordLayout(
        typ, tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields),
        layout.copyable, layout.movable))


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

    def get(self, node: object, typ: NominalType) -> MIRConstructorDefinition:
        definition = self.records.get(typ, "missing constructor definition")
        if isinstance(definition, str):
            raise MIRUnsupported(node, definition)
        return definition
