# tpy: macro_module
"""Dataclass macro for TurboPython.

This module implements @dataclass as a compile-time class macro.
It is loaded by the compiler via CPython (not compiled to C++).
"""

from tpyc.macro_api import (
    ClassInfo, FieldInfo, TypeInfo, CallMacroContext, MacroArg, MacroError,
    class_macro, call_macro,
    expr_to_cpp_default, ast, types, Expr, Stmt, Function, Type,
)
from _macro_helpers import (
    ORDER_DUNDERS, build_init, build_eq, build_repr, build_hash, build_order,
)

_MISSING = object()

# Module-level registry: tracks which records were built by @dataclass.
# Class macro writes here; call macros (asdict/astuple) read it.
_dataclass_records: set[str] = set()


class Field:
    """Descriptor returned by field(). Inspected by @dataclass macro."""

    def __init__(self, *, default=_MISSING, default_factory=_MISSING):
        if default is not _MISSING and default_factory is not _MISSING:
            raise TypeError("cannot specify both 'default' and 'default_factory'")
        if default is _MISSING and default_factory is _MISSING:
            raise TypeError("requires 'default' or 'default_factory'")
        self.default = default
        self.default_factory = default_factory


def field(*, default=_MISSING, default_factory=_MISSING) -> Field:
    """Declare field metadata in @dataclass classes."""
    return Field(default=default, default_factory=default_factory)


@class_macro
def dataclass(cls: ClassInfo, *, frozen: bool = False, order: bool = False) -> None:
    """Transform a class into a dataclass with auto-generated methods."""
    cls.is_frozen = frozen

    _process_field_defaults(cls)

    # Only inherit fields from parent @dataclass records
    parent_name = cls.parent.name if cls.parent else None
    if parent_name and parent_name in _dataclass_records:
        parent_fields = cls.get_parent_fields()
        _validate_frozen_consistency(cls, frozen)
    else:
        parent_fields = []
    all_fields = parent_fields + cls.fields
    has_parent = len(parent_fields) > 0

    # Synthesize __init__ (silently skipped when the user wrote one --
    # matches CPython, which also defers to an explicit __init__).
    if not cls.has_method("__init__"):
        if not all_fields:
            raise MacroError(
                f"@dataclass class '{cls.name}' must have at least one field annotation"
            )
        _validate_field_order(cls, all_fields)
        init_fn = build_init(cls, parent_fields, cls.fields)
        # CPython's generated __init__ ends with self.__post_init__().
        # Own-only check (not inherited): build_init chains super().__init__(),
        # which already runs an inherited __post_init__ once, so appending here
        # for an inherited hook would double-call it under TPy's static dispatch.
        if cls.has_method("__post_init__"):
            init_fn.body.append(
                ast.expr_stmt(ast.method_call(ast.name("self"), "__post_init__"))
            )
        cls.add_method(init_fn)

    # Synthesize __eq__ (silently skipped when the user wrote one).
    if all_fields and not cls.has_method("__eq__"):
        eq_fn = build_eq(cls, all_fields)
        eq_fn.hides_parent = has_parent
        cls.add_method(eq_fn)

    # Generate __repr__
    if all_fields and not cls.has_method("__repr__"):
        repr_fn = build_repr(cls, all_fields)
        repr_fn.hides_parent = has_parent
        cls.add_method(repr_fn)

    # Generate __hash__ for frozen dataclasses
    if frozen and all_fields and not cls.has_method("__hash__"):
        hash_fn = build_hash(cls, all_fields)
        hash_fn.hides_parent = has_parent
        cls.add_method(hash_fn)

    # Generate ordering methods
    if order and all_fields:
        _synthesize_order_methods(cls, all_fields, has_parent)

    cls.set_match_args([f.name for f in all_fields])
    _dataclass_records.add(cls.name)


def _process_field_defaults(cls: ClassInfo) -> None:
    """Unwrap Field descriptors set by field() calls."""
    for fld in cls.fields:
        if not isinstance(fld.default_obj, Field):
            continue
        spec = fld.default_obj
        if spec.default is not _MISSING:
            fld.set_default(spec.default, default_value=expr_to_cpp_default(spec.default))
        elif spec.default_factory is not _MISSING:
            factory_name = ast.get_name(spec.default_factory)
            if factory_name is None:
                raise MacroError(
                    "default_factory must be a type name (e.g., list, dict, MyRecord)",
                    loc=fld.loc,
                )
            factory_call = ast.call(factory_name)
            # Preserve source location on the generated call
            factory_call.loc = fld.loc
            fld.set_default(factory_call, is_factory=True)


def _validate_frozen_consistency(cls: ClassInfo, frozen: bool) -> None:
    """Validate frozen/non-frozen consistency across inheritance."""
    result = cls.is_parent_frozen()
    if result is None:
        return
    parent_frozen, parent_name = result
    if parent_frozen and not frozen:
        raise MacroError(
            f"Cannot inherit non-frozen @dataclass '{cls.name}' "
            f"from frozen @dataclass '{parent_name}'"
        )
    if not parent_frozen and frozen:
        raise MacroError(
            f"Cannot inherit frozen @dataclass '{cls.name}' "
            f"from non-frozen @dataclass '{parent_name}'"
        )


def _validate_field_order(cls: ClassInfo, all_fields: list[FieldInfo]) -> None:
    """Validate no non-default field follows a field with default."""
    seen_default = False
    for fld in all_fields:
        if fld.has_default:
            seen_default = True
        elif seen_default:
            raise MacroError(
                f"Field '{fld.name}' without default follows field with default "
                f"in @dataclass class '{cls.name}'",
                loc=fld.loc,
            )


def _synthesize_order_methods(
    cls: ClassInfo, all_fields: list[FieldInfo], has_parent: bool,
) -> None:
    """Add ordering methods for @dataclass(order=True)."""
    for dunder in ORDER_DUNDERS:
        if cls.has_method(dunder):
            raise MacroError(
                f"@dataclass(order=True) cannot overwrite '{dunder}' "
                f"defined in class '{cls.name}'"
            )
    for fn in build_order(cls, all_fields):
        fn.hides_parent = has_parent
        cls.add_method(fn)


# ---------------------------------------------------------------------------
# Call-site macros: asdict, astuple
# ---------------------------------------------------------------------------

@call_macro
def asdict(ctx: CallMacroContext, obj: MacroArg) -> Expr:
    """Convert a dataclass instance to a dict."""
    return _build_asdict(ctx, obj.expr, obj.type)


def _has_dc(ctx: CallMacroContext, type_info: TypeInfo) -> bool:
    """Check if a type contains dataclass instances needing recursion."""
    inner = type_info.unwrap_optional()
    if inner is not None:
        return _has_dc(ctx, inner)
    if type_info.is_record and type_info.name in _dataclass_records:
        return True
    # Check dict/tuple before iterable -- iterating a dict yields keys,
    # which would incorrectly match dict[DC_Key, V] as list[DC_Key].
    if type_info.is_dict and len(type_info.type_args) == 2:
        return any(_has_dc(ctx, t) for t in type_info.type_args)
    if type_info.is_tuple and type_info.type_args:
        return any(_has_dc(ctx, t) for t in type_info.type_args)
    # Skip sets: the result (dict) isn't hashable, so set[DC] -> set[DC]
    # unchanged (matches CPython). Only recurse into list-like iterables.
    if type_info.is_set:
        return False
    elem = ctx.get_iterable_element_type(type_info)
    if elem is not None and _has_dc(ctx, elem):
        return True
    return False


def _value_transform(
    ctx: CallMacroContext, access: Expr, fld_type: TypeInfo,
    dc_fn: 'Callable',
    value_fn: 'Callable',
) -> Expr:
    """Shared recursion for _asdict_value / _astuple_value.

    dc_fn: called for direct dataclass fields (e.g. _build_asdict or _build_astuple)
    value_fn: called recursively for nested elements (e.g. _asdict_value or _astuple_value)
    """
    # Optional[T] -> None if access is None else recurse(access, T)
    inner = fld_type.unwrap_optional()
    if inner is not None and _has_dc(ctx, inner):
        # Clone to avoid sharing AST nodes between condition and body --
        # sema sets resolved types on nodes, so reuse would cause overwrites.
        transformed = _value_transform(ctx, ast.clone(access), inner, dc_fn, value_fn)
        return ast.if_expr(
            ast.binop(access, "is not", ast.none_lit()),
            transformed,
            ast.none_lit(),
        )
    if fld_type.is_record and fld_type.name in _dataclass_records:
        return dc_fn(ctx, access, fld_type)
    # dict/tuple before iterable (see _has_dc comment)
    if fld_type.is_dict and len(fld_type.type_args) == 2:
        key_type, val_type = fld_type.type_args
        if _has_dc(ctx, key_type) or _has_dc(ctx, val_type):
            return _build_dict_comprehension(ctx, access, key_type, val_type, value_fn)
    if fld_type.is_tuple and fld_type.type_args:
        if any(_has_dc(ctx, t) for t in fld_type.type_args):
            return _build_tuple_expansion(ctx, access, fld_type.type_args, value_fn)
    elem = ctx.get_iterable_element_type(fld_type)
    if elem is not None and _has_dc(ctx, elem):
        var = ast.fresh_tmp("macro")
        return ast.list_comprehension(
            value_fn(ctx, ast.name(var), elem),
            ast.comprehension_generator(var, access),
        )
    return access


def _build_dict_comprehension(
    ctx: CallMacroContext, access: Expr,
    key_type: TypeInfo, val_type: TypeInfo,
    value_fn: 'Callable',
) -> Expr:
    """Expand dict field: {f(k): f(v) for k, v in field.items()}"""
    k_var = ast.fresh_tmp("macro")
    v_var = ast.fresh_tmp("macro")
    items_call = ast.method_call(access, "items")
    return ast.dict_comprehension(
        value_fn(ctx, ast.name(k_var), key_type),
        value_fn(ctx, ast.name(v_var), val_type),
        ast.comprehension_generator("__comp_tup", items_call,
                                    unpack_vars=[k_var, v_var]),
    )


def _build_tuple_expansion(
    ctx: CallMacroContext, access: Expr,
    elem_types: list[TypeInfo],
    value_fn: 'Callable',
) -> Expr:
    """Expand tuple field: (f(field[0]), f(field[1]), ...)"""
    elements = []
    for i, elem_type in enumerate(elem_types):
        subscript = ast.subscript(access, ast.int_lit(i))
        elements.append(value_fn(ctx, subscript, elem_type))
    return ast.tuple_lit(elements)


def _asdict_value(ctx: CallMacroContext, access: Expr, fld_type: TypeInfo) -> Expr:
    """Build the value expression for a single field in asdict expansion."""
    return _value_transform(ctx, access, fld_type, _build_asdict, _asdict_value)


def _build_asdict(ctx: CallMacroContext, expr: Expr, type_info: TypeInfo) -> Expr:
    if type_info.name not in _dataclass_records:
        raise MacroError(f"asdict() requires a @dataclass instance, got '{type_info.name}'")
    fields = ctx.get_record_fields(type_info.name)
    keys = []
    values = []
    value_tpy_types = []
    for fld in fields:
        keys.append(ast.str_lit(fld.name))
        access = ast.field_access(expr, fld.name)
        value = _asdict_value(ctx, access, fld.type)
        values.append(value)
        value_tpy_types.append(_asdict_result_type(ctx, fld.type))

    dict_literal = ast.dict_lit(keys, values)

    # For mixed-type fields, wrap in typed dict constructor: dict[str, A|B]({...})
    unique = list(dict.fromkeys(str(t) for t in value_tpy_types))
    if len(unique) > 1:
        value_type = types.union(tuple(value_tpy_types))
        return ast.call("dict", [dict_literal],
                        call_type=types.dict(types.str, value_type))
    return dict_literal


def _asdict_result_type(ctx: CallMacroContext, type_info: TypeInfo) -> Type:
    """Compute the result type for a field in asdict expansion."""
    # Optional[T] -> Optional[result_type(T)]
    inner = type_info.unwrap_optional()
    if inner is not None and _has_dc(ctx, inner):
        return types.optional(_asdict_result_type(ctx, inner))
    # Direct dataclass
    if type_info.is_record and type_info.name in _dataclass_records:
        fields = ctx.get_record_fields(type_info.name)
        if fields is None:
            return type_info.raw_type
        value_types = [_asdict_result_type(ctx, fld.type) for fld in fields]
        unique = list(dict.fromkeys(str(t) for t in value_types))
        if len(unique) > 1:
            return types.dict(types.str, types.union(tuple(value_types)))
        return types.dict(types.str, value_types[0])
    # dict/tuple before iterable (same ordering as _has_dc)
    if type_info.is_dict and len(type_info.type_args) == 2:
        key_type, val_type = type_info.type_args
        if _has_dc(ctx, key_type) or _has_dc(ctx, val_type):
            return types.dict(
                _asdict_result_type(ctx, key_type),
                _asdict_result_type(ctx, val_type),
            )
    if type_info.is_tuple and type_info.type_args:
        if any(_has_dc(ctx, t) for t in type_info.type_args):
            return types.tuple(tuple(
                _asdict_result_type(ctx, t) for t in type_info.type_args
            ))
    # list[Dataclass] -> list[dict[...]] (sets excluded by _has_dc)
    elem = ctx.get_iterable_element_type(type_info)
    if elem is not None and _has_dc(ctx, elem):
        return types.list(_asdict_result_type(ctx, elem))
    return type_info.raw_type


@call_macro
def astuple(ctx: CallMacroContext, obj: MacroArg) -> Expr:
    """Convert a dataclass instance to a tuple."""
    return _build_astuple(ctx, obj.expr, obj.type)


def _build_astuple(ctx: CallMacroContext, expr: Expr, type_info: TypeInfo) -> Expr:
    # Result type is inferred by sema from the element types;
    # no explicit type annotation needed (unlike _build_asdict for mixed fields).
    if type_info.name not in _dataclass_records:
        raise MacroError(f"astuple() requires a @dataclass instance, got '{type_info.name}'")
    fields = ctx.get_record_fields(type_info.name)
    elements = []
    for fld in fields:
        access = ast.field_access(expr, fld.name)
        elements.append(_astuple_value(ctx, access, fld.type))
    return ast.tuple_lit(elements)


def _astuple_value(ctx: CallMacroContext, access: Expr, fld_type: TypeInfo) -> Expr:
    """Build the value expression for a single field in astuple expansion."""
    return _value_transform(ctx, access, fld_type, _build_astuple, _astuple_value)
