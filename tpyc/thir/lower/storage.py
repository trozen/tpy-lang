"""Semantic storage facts recorded alongside the selected lowering operation."""

from typing import TYPE_CHECKING

from ...parse.nodes import TpyFieldAccess, TpyName, TpySubscript, TupleElemCapture
from ...typesys import (
    BOOL, INT32, NominalType, OptionalType, ReadonlyType, TupleType, TpyType,
    unwrap_readonly, unwrap_ref_type,
)
from ..nodes import (
    THIRAliasBinding, THIRBorrowedRecord, THIRExpr, THIRFieldIdentity, THIRName,
    THIROptionalLayout, THIRRecordLayout, THIRSubscript, THIRTupleLayout,
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


def direct_field(expr: TpyFieldAccess,
                 analyzer: 'SemanticAnalyzer',
                 receiver: THIRExpr | None = None) -> THIRFieldIdentity | None:
    tuple_receiver = (isinstance(expr.obj, TpySubscript)
                      and isinstance(receiver, THIRSubscript)
                      and receiver.tuple_index is not None)
    if ((not isinstance(expr.obj, TpyName) and not tuple_receiver)
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
    if member is None or member.type not in (BOOL, INT32):
        return None
    return THIRFieldIdentity(reference.type, member.name, member.type)
