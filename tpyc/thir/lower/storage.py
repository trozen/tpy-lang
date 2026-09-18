"""Semantic storage facts recorded alongside the selected lowering operation."""

from typing import TYPE_CHECKING

from ...parse.nodes import TpyFieldAccess, TpyName, TpySubscript, TupleElemCapture
from ...typesys import (
    BOOL, INT32, INT32_MIN, INT32_MAX, NominalType, OptionalType, ReadonlyType, TupleType, TpyType,
    UnionType, is_void_like_type, unwrap_readonly, unwrap_ref_type,
)
from ..nodes import (
    Form, THIRAliasBinding, THIRBorrowedRecord, THIRExpr, THIRFieldAccess, THIRFieldIdentity, THIRName,
    THIROptionalLayout, THIRRecordLayout, THIRSubscript, THIRTupleLayout, THIRUnionLayout,
    THIRCoerce, THIRLiteral, THIRUnionLiteral,
)

if TYPE_CHECKING:
    from ...sema.analyzer import SemanticAnalyzer


def borrowed_record(typ: TpyType, readonly: bool,
                    analyzer: 'SemanticAnalyzer') -> THIRBorrowedRecord | None:
    typ = unwrap_readonly(unwrap_ref_type(typ))
    if not isinstance(typ, NominalType) or typ.type_args or typ.is_protocol:
        return None
    info = analyzer.registry.get_record_for_type(typ)
    if (info is None or info.is_native or info.is_value_type or info.is_typed_dict
            or info.parents or info.type_params):
        return None
    return THIRBorrowedRecord(typ, readonly)


def alias_binding(source: THIRExpr, typ: TpyType, readonly: bool,
                  analyzer: 'SemanticAnalyzer') -> THIRAliasBinding | None:
    if not isinstance(source, THIRName):
        return None
    reference = borrowed_record(typ, readonly, analyzer)
    if reference is None:
        return None
    if unwrap_readonly(unwrap_ref_type(source.result_type)) != reference.type:
        return None
    return THIRAliasBinding(source.name, reference)


def record_layout(typ: TpyType, analyzer: 'SemanticAnalyzer') -> THIRRecordLayout | None:
    reference = borrowed_record(typ, False, analyzer)
    if reference is None:
        return None
    info = analyzer.registry.get_record_for_type(reference.type)
    return THIRRecordLayout(
        reference.type,
        tuple(THIRFieldIdentity(reference.type, f.name, f.type) for f in info.fields),
        info.has_init and len(info.get_method_overloads("__init__")) == 1,
        info.has_copy, info.has_move, info.has_del,
        not info.is_nocopy, info.is_movable and info.move_override is not False,
    )


def storage_borrow(source: THIRExpr, typ: TpyType, readonly: bool,
                   analyzer: 'SemanticAnalyzer') -> THIRBorrowedRecord | None:
    if (not isinstance(source, THIRFieldAccess) or source.field_identity is None
            or source.form is not Form.STORAGE):
        return None
    reference = borrowed_record(typ, readonly, analyzer)
    if (reference is None or unwrap_readonly(source.field_identity.type) != reference.type
            or unwrap_readonly(unwrap_ref_type(source.result_type)) != reference.type):
        return None
    return reference


def tuple_layout(typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                 captures: tuple[TupleElemCapture, ...] | None = None,
                 borrow: bool = False, readonly: bool = False) -> THIRTupleLayout | None:
    """Describe only selected flat payloads, never infer ownership from a tuple type."""
    if not isinstance(typ, TupleType):
        return None
    if captures is not None and len(captures) != len(typ.element_types):
        return None
    elements: list[TpyType | THIRBorrowedRecord] = []
    for i, element in enumerate(typ.element_types):
        mode = captures[i] if captures is not None else (
            TupleElemCapture.REF if borrow else TupleElemCapture.VALUE)
        if element in (BOOL, INT32):
            if captures is not None and mode is not TupleElemCapture.VALUE:
                return None
            elements.append(element)
        else:
            if mode not in (TupleElemCapture.REF, TupleElemCapture.CONST_REF):
                return None
            reference = borrowed_record(
                element, readonly or mode is TupleElemCapture.CONST_REF
                or isinstance(element, ReadonlyType), analyzer)
            if reference is None:
                return None
            elements.append(reference)
    return THIRTupleLayout(tuple(elements))


def optional_layout(typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                    borrow: bool, readonly: bool = False) -> THIROptionalLayout | None:
    outer_readonly = isinstance(unwrap_ref_type(typ), ReadonlyType)
    typ = unwrap_readonly(unwrap_ref_type(typ))
    if not isinstance(typ, OptionalType) or typ.force_pointer_repr:
        return None
    inner = unwrap_readonly(typ.inner)
    if inner in (BOOL, INT32):
        return THIROptionalLayout(inner) if not typ.uses_pointer_repr() else None
    if not borrow:
        return None
    reference = borrowed_record(typ.inner, readonly or outer_readonly
                                or isinstance(typ.inner, ReadonlyType), analyzer)
    return THIROptionalLayout(reference) if reference is not None else None


def union_layout(typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                 borrow: bool, readonly: bool = False) -> THIRUnionLayout | None:
    outer_readonly = isinstance(unwrap_ref_type(typ), ReadonlyType)
    typ = unwrap_readonly(unwrap_ref_type(typ))
    if not isinstance(typ, UnionType) or typ.needs_wrapper():
        return None
    if len({unwrap_readonly(m) for m in typ.members}) != len(typ.members):
        return None
    if typ.uses_pointer_repr() != borrow:
        return None
    elements: list[TpyType | THIRBorrowedRecord | None] = []
    for member in typ.members:
        if is_void_like_type(member):
            elements.append(None)
        elif borrow:
            if (outer_readonly or isinstance(member, ReadonlyType)) and not readonly:
                return None
            reference = borrowed_record(member, readonly, analyzer)
            if reference is None:
                return None
            elements.append(reference)
        elif member in (BOOL, INT32):
            elements.append(member)
        else:
            return None
    return THIRUnionLayout(typ, tuple(elements))


def union_literal(expr: THIRExpr | None, layout: THIRUnionLayout) -> THIRUnionLiteral | None:
    if isinstance(expr, THIRCoerce):
        if expr.coercion_name != "int_literal_to_fixed_int" or expr.wrap is not None:
            return None
        expr = expr.expr
    if not isinstance(expr, THIRLiteral):
        return None
    if type(expr.value) is int and not INT32_MIN <= expr.value <= INT32_MAX:
        return None
    typ = BOOL if type(expr.value) is bool else INT32 if type(expr.value) is int else None
    if (expr.value is None or typ is not None) and typ in layout.elements:
        return THIRUnionLiteral(layout, layout.elements.index(typ), expr.value)
    return None


def direct_field(expr: TpyFieldAccess,
                 analyzer: 'SemanticAnalyzer',
                 receiver: THIRExpr | None = None) -> THIRFieldIdentity | None:
    tuple_receiver = (isinstance(expr.obj, TpySubscript)
                      and isinstance(receiver, THIRSubscript)
                      and receiver.tuple_index is not None)
    field_receiver = (isinstance(expr.obj, TpyFieldAccess)
                      and isinstance(receiver, THIRFieldAccess)
                      and receiver.field_identity is not None)
    if ((not isinstance(expr.obj, TpyName) and not tuple_receiver and not field_receiver)
            or expr.hidden_call is not None
            or expr.deref_depth or expr.needs_optional_runtime_check
            or expr.unbound_self_parent_type is not None
            or expr.class_constant_owner is not None
            or expr.native_field_name is not None or expr.accessed_field_is_interior):
        return None
    typ = analyzer.get_expr_type(expr.obj)
    reference = borrowed_record(typ, False, analyzer) if typ is not None else None
    if reference is None:
        return None
    info = analyzer.registry.get_record_for_type(reference.type)
    member = next((f for f in info.fields if f.name == expr.field), None)
    if member is None or (member.type not in (BOOL, INT32)
                          and borrowed_record(member.type, False, analyzer) is None):
        return None
    return THIRFieldIdentity(reference.type, member.name, member.type)
