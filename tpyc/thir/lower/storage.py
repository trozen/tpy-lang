"""Semantic storage facts recorded alongside the selected lowering operation."""

from dataclasses import replace
from typing import TYPE_CHECKING

from ...symbol_binding import SymbolKind
from ...type_def_registry import is_list, is_array, is_set, is_dict, zero_value_of

from ...parse.nodes import TpyFieldAccess, TpyName, TpySubscript, TupleElemCapture
from ...typesys import (
    NominalType, OptionalType, ReadonlyType, TupleType, TpyType,
    UnionType, is_void_like_type, unwrap_readonly, unwrap_ref_type,
)
from ..scalar_leaves import leaf_constant, leaf_global, storage_leaf
from ..nodes import (
    Form, THIRAliasBinding, THIRBorrowedRecord, THIRExpr, THIRFieldAccess, THIRFieldIdentity, THIRName,
    THIROptionalLayout, THIRRecordLayout, THIRSubscript, THIRTupleLayout, THIRUnionLayout,
    THIRCoerce, THIRLiteral, THIRSelf, THIRUnionLiteral, THIRGlobalBinding, THIRHoistedBinding,
    THIRWrapperDefault, THIROwnedRecord,
    THIRNativeContainer, THIRCtorCall,
)

if TYPE_CHECKING:
    from ...sema.analyzer import SemanticAnalyzer
    from .context import _LowerCtx


def global_name_binding(name: str, typ: TpyType, lc: '_LowerCtx') -> THIRGlobalBinding | None:
    analyzer = lc.analyzer
    if (not lc.global_binding_scope or lc.top_level_scope or lc.capture_funcs or name in lc.prescan.param_names
            or not leaf_global(typ) or not lc.prescan.binds_global(name)
            or name not in analyzer.ctx.top_level_decls or name in lc.prescan.native_globals):
        return None
    declared = analyzer.ctx.global_scope.lookup(name)
    if declared is None:
        binding = analyzer.global_ns.lookup_local(name)
        declared = binding.type if binding is not None else None
    if declared != typ:
        return None
    return THIRGlobalBinding(analyzer.ctx.cpp_module_name, name, typ,
                             name in lc.prescan.global_seeded)


def module_global_binding(module: str, name: str, typ: TpyType,
                          analyzer: 'SemanticAnalyzer') -> THIRGlobalBinding | None:
    info = analyzer.registry.get_module(module)
    if not leaf_global(typ) or info is None or info.module_attributes is None:
        return None
    cell = info.module_attributes.get(name)
    variable = info.variables.get(name)
    if (cell is None or cell.binding.kind is not SymbolKind.VARIABLE
            or cell.binding.defining_module is not None or variable is None
            or variable.native_cpp_name is not None or variable.is_pointer or variable.type != typ):
        return None
    # Reexports do not preserve Python's imported binding snapshot.
    return THIRGlobalBinding(module, cell.binding.canonical_name, typ)


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


def native_container(typ: TpyType, readonly: bool,
                     analyzer: 'SemanticAnalyzer') -> THIRNativeContainer | None:
    readonly = readonly or isinstance(unwrap_ref_type(typ), ReadonlyType)
    typ = unwrap_readonly(unwrap_ref_type(typ))
    if not isinstance(typ, NominalType) or not typ.type_args:
        return None
    args = typ.type_args
    if not ((is_list(typ) or is_set(typ)) and len(args) == 1
            or is_dict(typ) and len(args) == 2 and all(storage_leaf(a) for a in args)
            or is_array(typ) and len(args) == 2 and type(args[1]) is int and args[1] >= 0):
        return None
    element = args[0]
    if not storage_leaf(element):
        if not (is_list(typ) or is_array(typ)) or unwrap_readonly(unwrap_ref_type(element)) != element:
            return None
        element = borrowed_record(element, readonly, analyzer)
        if element is None:
            return None
        record = analyzer.registry.get_record_for_type(element.type)
        if any(not storage_leaf(f.type) for f in record.fields):
            return None
    return THIRNativeContainer(typ, element, readonly)


def hoisted_binding(name: str, typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                    borrow: bool = False, readonly: bool = False) -> THIRHoistedBinding | None:
    if storage_leaf(typ) and not borrow:
        return THIRHoistedBinding(name, typ)
    if isinstance(typ, TupleType):
        layout = tuple_layout(typ, analyzer, borrow=borrow, readonly=readonly)
        return THIRHoistedBinding(name, typ, tuple_layout=layout) if layout is not None else None
    if not borrow:
        if isinstance(typ, OptionalType):
            layout = optional_layout(typ, analyzer, borrow=False, readonly=readonly)
            if layout is not None:
                return THIRHoistedBinding(name, typ, optional_layout=layout,
                                         physical_default=THIRWrapperDefault(0, None))
        if isinstance(typ, UnionType):
            layout = union_layout(typ, analyzer, borrow=False, readonly=readonly)
            if layout is not None:
                first = layout.elements[0]
                default = None if first is None else zero_value_of(first)
                # A first member with no declared zero (an enum) has no
                # physical default to publish, so the hoist stays unmodeled.
                if first is not None and default is None:
                    return None
                return THIRHoistedBinding(name, typ, union_layout=layout,
                                         physical_default=THIRWrapperDefault(0, default))
        return None
    if isinstance(typ, OptionalType) and typ.uses_pointer_repr():
        layout = optional_layout(typ, analyzer, borrow=True, readonly=readonly)
        return THIRHoistedBinding(name, typ, optional_layout=layout) if layout is not None else None
    reference = borrowed_record(typ, readonly, analyzer)
    return THIRHoistedBinding(name, typ, borrowed_record=reference) if reference is not None else None


def alias_binding(source: THIRExpr, typ: TpyType, readonly: bool,
                  analyzer: 'SemanticAnalyzer') -> THIRAliasBinding | None:
    if not isinstance(source, (THIRName, THIRSelf)):
        return None
    reference = borrowed_record(typ, readonly, analyzer)
    if reference is None:
        return None
    if unwrap_readonly(unwrap_ref_type(source.result_type)) != reference.type:
        return None
    return THIRAliasBinding(source.name if isinstance(source, THIRName) else "self", reference)


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
    match source:
        case THIRFieldAccess() if source.field_identity is not None and source.form is Form.STORAGE:
            selected = source.field_identity.type
        case THIRSubscript() if source.deref and source.tuple_index is not None and source.form is Form.BORROW:
            tuple_type = unwrap_readonly(unwrap_ref_type(source.receiver.result_type))
            if (not isinstance(source.receiver, THIRName) or not isinstance(tuple_type, TupleType)
                    or not 0 <= source.tuple_index < len(tuple_type.element_types)):
                return None
            selected = tuple_type.element_types[source.tuple_index]
        case _:
            return None
    reference = borrowed_record(typ, readonly, analyzer)
    if (reference is None or unwrap_readonly(unwrap_ref_type(selected)) != reference.type
            or unwrap_readonly(unwrap_ref_type(source.result_type)) != reference.type):
        return None
    return reference


def tuple_layout(typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                 captures: tuple[TupleElemCapture, ...] | None = None,
                 borrow: bool = False, readonly: bool = False,
                 own_records: bool = False) -> THIRTupleLayout | None:
    """Describe only selected flat payloads, never infer ownership from a tuple type."""
    if not isinstance(typ, TupleType):
        return None
    if captures is not None and len(captures) != len(typ.element_types):
        return None
    elements: list[TpyType | THIRBorrowedRecord | THIROwnedRecord] = []
    for i, element in enumerate(typ.element_types):
        mode = captures[i] if captures is not None else (
            TupleElemCapture.REF if borrow else TupleElemCapture.VALUE)
        if storage_leaf(element):
            if captures is not None and mode is not TupleElemCapture.VALUE:
                return None
            elements.append(element)
        else:
            if own_records and captures is not None and mode is TupleElemCapture.VALUE:
                reference = borrowed_record(element, readonly, analyzer)
                if reference is None:
                    return None
                elements.append(THIROwnedRecord(reference.type, reference.readonly))
                continue
            if mode not in (TupleElemCapture.REF, TupleElemCapture.CONST_REF):
                return None
            reference = borrowed_record(
                element, readonly or mode is TupleElemCapture.CONST_REF
                or isinstance(element, ReadonlyType), analyzer)
            if reference is None:
                return None
            elements.append(reference)
    return THIRTupleLayout(tuple(elements))


def tuple_parameter_layout(typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                           readonly: bool) -> THIRTupleLayout | None:
    # Ref describes parameter passing; Own must survive normalization and fail eligibility.
    typ = unwrap_ref_type(typ)
    readonly = readonly or isinstance(typ, ReadonlyType)
    typ = unwrap_ref_type(unwrap_readonly(typ))
    return tuple_layout(typ, analyzer, borrow=True, readonly=readonly)


def optional_layout(typ: TpyType, analyzer: 'SemanticAnalyzer', *,
                    borrow: bool, readonly: bool = False) -> THIROptionalLayout | None:
    outer_readonly = isinstance(unwrap_ref_type(typ), ReadonlyType)
    typ = unwrap_readonly(unwrap_ref_type(typ))
    if not isinstance(typ, OptionalType) or typ.force_pointer_repr:
        return None
    inner = unwrap_readonly(typ.inner)
    if storage_leaf(inner):
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
        elif storage_leaf(member):
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
    if expr.value is None:
        return THIRUnionLiteral(layout, layout.elements.index(None), None) if None in layout.elements else None
    # The literal's own type names its alternative when it is one; otherwise
    # exactly one alternative may hold the value.
    if expr.result_type in layout.elements and leaf_constant(expr.result_type, expr.value):
        return THIRUnionLiteral(layout, layout.elements.index(expr.result_type), expr.value)
    holders = [i for i, m in enumerate(layout.elements) if m is not None and leaf_constant(m, expr.value)]
    return THIRUnionLiteral(layout, holders[0], expr.value) if len(holders) == 1 else None


def full_expression_record(expr: THIRExpr, analyzer: 'SemanticAnalyzer') -> THIRExpr:
    if not isinstance(expr, THIRCtorCall) or expr.form is not Form.STORAGE or expr.brace_init:
        return expr
    layout = record_layout(expr.result_type, analyzer)
    if (layout is None or layout.type != expr.result_type or not layout.unique_constructor
            or layout.custom_copy or layout.custom_move or layout.custom_destructor
            or any(not storage_leaf(f.type) for f in layout.fields)):
        return expr
    return replace(expr, full_expression_storage=THIROwnedRecord(layout.type))


def direct_field(expr: TpyFieldAccess,
                 analyzer: 'SemanticAnalyzer',
                 receiver: THIRExpr | None = None) -> THIRFieldIdentity | None:
    tuple_receiver = (isinstance(expr.obj, TpySubscript)
                      and isinstance(receiver, THIRSubscript)
                      and receiver.tuple_index is not None)
    field_receiver = (isinstance(expr.obj, TpyFieldAccess)
                      and isinstance(receiver, THIRFieldAccess)
                      and receiver.field_identity is not None)
    temporary_receiver = isinstance(receiver, THIRCtorCall) and receiver.full_expression_storage is not None
    if ((not isinstance(expr.obj, TpyName) and not tuple_receiver and not field_receiver and not temporary_receiver)
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
    if member is None or (not storage_leaf(member.type)
                          and borrowed_record(member.type, False, analyzer) is None):
        return None
    return THIRFieldIdentity(reference.type, member.name, member.type)
