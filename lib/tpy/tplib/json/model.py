# tpy: macro_module
"""JSON model macro for TurboPython.

Provides @model class macro that bundles @dataclass behavior with
JSON serialization/deserialization via tplib.json.JsonReader/JsonWriter.
"""

from tpyc.macro_api import (
    ClassInfo, FieldInfo, TypeInfo, MacroError,
    class_macro,
    build_init, build_eq,
)
from tpyc.parse import (
    TpyFunction, TpyExpr, TpyStmt,
    TpyAssign, TpyFieldAccess, TpyName, TpyBinOp,
    TpyReturn, TpyMethodCall, TpyCall, TpyExprStmt,
    TpyStrLiteral, TpyBoolLiteral, TpyNoneLiteral,
    TpyIntLiteral, TpyFloatLiteral,
    TpyVarDecl, TpyTupleUnpack, TpyIf, TpyWhile, TpyAssert,
    TpySubscript, TpyTupleLiteral, TpyForEach,
    TpyArrayLiteral, TpyDictLiteral,
    TpyMatch, TpyMatchCase, TpyLiteralPattern, TpyWildcardPattern,
)
from tpyc.typesys import (
    NamedType, OwnType, VoidType, BoolType, StrType, StrViewType,
    FloatType, Float32Type,
    FixedIntType, OptionalType, ListType, DictType, TupleType,
    EnumType, BigIntType,
    INT64, UINT64,
)


# ---------------------------------------------------------------------------
# Type classification helpers
# ---------------------------------------------------------------------------

_tmp_counter = 0

def _fresh_tmp(hint: str = "v") -> str:
    global _tmp_counter
    _tmp_counter += 1
    return f"__{hint}_{_tmp_counter}"


def _is_int_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, FixedIntType)


def _is_bigint_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, BigIntType)


def _is_float_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, (FloatType, Float32Type))


def _is_str_type(ti: TypeInfo) -> bool:
    return ti.is_str


def _is_bool_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, BoolType)


def _is_list_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, ListType)


def _is_dict_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, DictType)


def _is_tuple_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, TupleType)


def _is_model_record(ti: TypeInfo) -> bool:
    """Check if a type is a user record (potential nested @model)."""
    return ti.is_record


def _is_enum_type(ti: TypeInfo) -> bool:
    return isinstance(ti._tpy_type, EnumType)


def _unwrap_optional(ti: TypeInfo) -> TypeInfo | None:
    """If ti is Optional[T], return T. Otherwise return None."""
    if ti.is_optional and isinstance(ti._tpy_type, OptionalType):
        return TypeInfo.from_tpy_type(ti._tpy_type.inner)
    return None


# ---------------------------------------------------------------------------
# AST construction helpers
# ---------------------------------------------------------------------------

def _method_call(obj: TpyExpr, method: str, args: list[TpyExpr] | None = None) -> TpyExpr:
    return TpyMethodCall(obj=obj, method=method, args=args or [])


def _call(func: str, args: list[TpyExpr] | None = None) -> TpyExpr:
    return TpyCall(func=func, args=args or [])


def _name(n: str) -> TpyName:
    return TpyName(n)


def _str_lit(s: str) -> TpyStrLiteral:
    return TpyStrLiteral(value=s)


def _eq(left: TpyExpr, right: TpyExpr) -> TpyBinOp:
    return TpyBinOp(left=left, op="==", right=right)


def _expr_stmt(expr: TpyExpr) -> TpyExprStmt:
    return TpyExprStmt(expr=expr)


def _reader_name() -> TpyName:
    return _name("__reader")


def _writer_name() -> TpyName:
    return _name("__writer")


# ---------------------------------------------------------------------------
# from_json / _from_reader body generation
# ---------------------------------------------------------------------------

def _build_read_into(fld_type: TypeInfo, reader: TpyExpr, hint: str = "v") -> tuple[list[TpyStmt], str]:
    """Read any JSON value into a fresh temp variable.

    Returns (statements, var_name). Works for all supported types
    including containers and enums.
    """
    # Simple expression types -- single TpyVarDecl
    expr = _try_build_read_expr(fld_type, reader)
    if expr is not None:
        tmp = _fresh_tmp(hint)
        return ([TpyVarDecl(name=tmp, type=None, init=expr)], tmp)

    # Enum: try_parse returns Optional, unwrap with assert
    if _is_enum_type(fld_type):
        parsed = _fresh_tmp("parsed")
        tmp = _fresh_tmp(hint)
        enum_name = fld_type._tpy_type.name
        return ([
            TpyVarDecl(name=parsed, type=None,
                       init=_call("try_parse", [_name(enum_name),
                                                _method_call(reader, "read_str")])),
            TpyAssert(
                condition=TpyBinOp(left=_name(parsed), op="is not",
                                   right=TpyNoneLiteral()),
                message=_str_lit(f"json: invalid enum value"),
            ),
            TpyVarDecl(name=tmp, type=fld_type._tpy_type, init=_name(parsed)),
        ], tmp)

    # Container types: declare with default, read in-place
    tmp = _fresh_tmp(hint)
    default = _default_for_type(fld_type)
    stmts: list[TpyStmt] = [TpyVarDecl(name=tmp, type=fld_type._tpy_type, init=default)]
    stmts.extend(_build_read_value_stmts(tmp, fld_type, reader))
    return (stmts, tmp)


def _try_build_read_expr(fld_type: TypeInfo, reader: TpyExpr) -> TpyExpr | None:
    """Try to build a single expression that reads a value. Returns None for
    types that need multiple statements (enums, containers, tuples)."""
    if _is_str_type(fld_type):
        return _method_call(reader, "read_str")
    if _is_bool_type(fld_type):
        return _method_call(reader, "read_bool")
    if _is_float_type(fld_type):
        read_call = _method_call(reader, "read_float")
        if isinstance(fld_type._tpy_type, Float32Type):
            return TpyCall(func="Float32", args=[read_call])
        return read_call
    if _is_int_type(fld_type):
        read_call = _method_call(reader, "read_int")
        type_name = str(fld_type._tpy_type)
        if type_name != "Int64":
            return TpyCall(func=type_name, args=[read_call])
        return read_call
    if _is_bigint_type(fld_type):
        # Read as string, parse via int()
        return _call("int", [_method_call(reader, "read_str")])
    if _is_model_record(fld_type):
        # The record must also be a @model (have _from_reader).
        # We can't check this at macro time since the other class may not
        # be registered yet, but the C++ error will be clear enough.
        return _method_call(_name(fld_type.name), "_from_reader", [reader])
    return None


def _default_for_type(ti: TypeInfo) -> TpyExpr:
    """Build a default initializer expression for a type."""
    if _is_str_type(ti):
        return _str_lit("")
    if _is_bool_type(ti):
        return TpyBoolLiteral(value=False)
    if _is_int_type(ti) or _is_bigint_type(ti):
        return TpyIntLiteral(value=0)
    if _is_float_type(ti):
        return TpyFloatLiteral(value=0.0)
    if _is_list_type(ti):
        return TpyArrayLiteral(elements=[])
    if _is_dict_type(ti):
        return TpyDictLiteral(keys=[], values=[])
    if _unwrap_optional(ti) is not None:
        return TpyNoneLiteral()
    raise MacroError(f"@model: no default for type '{ti.name}'")


def _build_read_value_stmts(
    fld_name: str, fld_type: TypeInfo, reader: TpyExpr,
) -> list[TpyStmt]:
    """Build statements to read a field value and assign to local var.

    Handles all types: Optional, list, dict, tuple, enum, primitives, models.
    """
    target = _name(fld_name)

    # Optional[T]: check for null, then read inner
    inner = _unwrap_optional(fld_type)
    if inner is not None:
        peek = _method_call(reader, "peek")
        none_token = TpyFieldAccess(obj=_name("JsonToken"), field="NONE")
        read_null = _expr_stmt(_method_call(reader, "read_null"))
        else_body = _read_and_assign(fld_name, inner, reader)
        return [TpyIf(
            condition=_eq(peek, none_token),
            then_body=[read_null],
            else_body=else_body,
        )]

    # list[T]: read array, append each element
    if _is_list_type(fld_type) and fld_type.type_args:
        elem_type = fld_type.type_args[0]
        loop_body = _read_and_append(target, elem_type, reader)
        return [
            _expr_stmt(_method_call(reader, "read_array_start")),
            TpyWhile(condition=_method_call(reader, "has_next"), body=loop_body),
            _expr_stmt(_method_call(reader, "read_array_end")),
        ]

    # dict[str, V]: read object, set each key-value
    if _is_dict_type(fld_type) and fld_type.type_args and len(fld_type.type_args) == 2:
        val_type = fld_type.type_args[1]
        dk_var = _fresh_tmp("dk")
        loop_body: list[TpyStmt] = [
            TpyVarDecl(name=dk_var, type=StrType(),
                       init=_method_call(reader, "read_key")),
        ]
        loop_body.extend(_read_and_assign_subscript(target, dk_var, val_type, reader))
        return [
            _expr_stmt(_method_call(reader, "read_object_start")),
            TpyWhile(condition=_method_call(reader, "has_next"), body=loop_body),
            _expr_stmt(_method_call(reader, "read_object_end")),
        ]

    # tuple[T1, T2, ...]: read as JSON array, positional
    if _is_tuple_type(fld_type):
        elem_types = [TypeInfo.from_tpy_type(et) for et in fld_type._tpy_type.element_types]
        stmts: list[TpyStmt] = [_expr_stmt(_method_call(reader, "read_array_start"))]
        elem_vars: list[str] = []
        for i, et in enumerate(elem_types):
            es, ev = _build_read_into(et, reader, f"t{i}")
            stmts.extend(es)
            elem_vars.append(ev)
            if i < len(elem_types) - 1:
                stmts.append(TpyAssert(
                    condition=_method_call(reader, "has_next"),
                    message=_str_lit(f"json: tuple expects {len(elem_types)} elements"),
                ))
        stmts.append(_expr_stmt(_method_call(reader, "read_array_end")))
        stmts.append(TpyAssign(
            target=target,
            value=TpyTupleLiteral(elements=[_name(v) for v in elem_vars]),
        ))
        return stmts

    # Simple types: direct assignment without temp var
    return _read_and_assign(fld_name, fld_type, reader)


def _read_and_assign(fld_name: str, fld_type: TypeInfo, reader: TpyExpr) -> list[TpyStmt]:
    """Read a value and assign to fld_name. Uses direct expression when possible,
    falls back to _build_read_into for complex types (enums, containers)."""
    expr = _try_build_read_expr(fld_type, reader)
    if expr is not None:
        return [TpyAssign(target=_name(fld_name), value=expr)]
    stmts, var = _build_read_into(fld_type, reader, fld_name)
    stmts.append(TpyAssign(target=_name(fld_name), value=_name(var)))
    return stmts


def _read_and_append(target: TpyExpr, elem_type: TypeInfo, reader: TpyExpr) -> list[TpyStmt]:
    """Read one element and append to target. Direct expression when possible."""
    expr = _try_build_read_expr(elem_type, reader)
    if expr is not None:
        return [_expr_stmt(_method_call(target, "append", [expr]))]
    stmts, var = _build_read_into(elem_type, reader, "elem")
    stmts.append(_expr_stmt(_method_call(target, "append", [_name(var)])))
    return stmts


def _read_and_assign_subscript(
    target: TpyExpr, key_var: str, val_type: TypeInfo, reader: TpyExpr,
) -> list[TpyStmt]:
    """Read one value and assign to target[key_var]. Direct expression when possible."""
    expr = _try_build_read_expr(val_type, reader)
    if expr is not None:
        return [TpyAssign(
            target=TpySubscript(obj=target, index=_name(key_var)),
            value=expr,
        )]
    stmts, var = _build_read_into(val_type, reader, "dv")
    stmts.append(TpyAssign(
        target=TpySubscript(obj=target, index=_name(key_var)),
        value=_name(var),
    ))
    return stmts


def _build_field_default(fld: FieldInfo) -> TpyExpr | None:
    """Build the default initializer for a local variable before parsing.

    Returns None for types that have no sensible zero value (model records,
    enums, tuples) -- these are required fields.
    """
    if fld.has_default and fld.default_expr is not None:
        return fld.default_expr
    ti = fld.type
    if _is_model_record(ti) or _is_enum_type(ti) or _is_tuple_type(ti):
        return None
    try:
        return _default_for_type(ti)
    except MacroError:
        return None


def _build_from_reader(cls: ClassInfo, all_fields: list[FieldInfo]) -> TpyFunction:
    """Build _from_reader(reader: JsonReader) -> Self static method."""
    reader = _reader_name()
    reader_type = NamedType("JsonReader")
    body: list[TpyStmt] = []

    # __reader.read_object_start()
    body.append(_expr_stmt(_method_call(reader, "read_object_start")))

    # Declare local vars for each field with defaults.
    # For required model-type fields (no default, not a primitive), declare as
    # Optional and assert non-None after parsing -- narrowing makes the type safe.
    # Fields with no zero value (models, enums, tuples) are declared as
    # Optional and asserted non-None after parsing.
    required_fields: list[str] = []
    for fld in all_fields:
        default = _build_field_default(fld)
        if default is not None:
            body.append(TpyVarDecl(
                name=fld.name, type=fld.type._tpy_type, init=default,
            ))
        elif _is_model_record(fld.type) or _is_enum_type(fld.type) or _is_tuple_type(fld.type):
            body.append(TpyVarDecl(
                name=fld.name, type=OptionalType(fld.type._tpy_type),
                init=TpyNoneLiteral(),
            ))
            required_fields.append(fld.name)
        else:
            raise MacroError(
                f"@model field '{fld.name}' of type '{fld.type.name}' "
                f"has no default and no sensible zero value",
                loc=fld.loc,
            )

    # Build match/case dispatch for field keys
    key_var = "__key"
    if all_fields:
        dispatch = _build_field_dispatch(all_fields, reader)
        loop_body: list[TpyStmt] = [
            TpyVarDecl(name=key_var, type=StrViewType(),
                       init=_method_call(reader, "read_key_raw")),
            dispatch,
        ]
        body.append(TpyWhile(
            condition=_method_call(reader, "has_next"),
            body=loop_body,
        ))

    # __reader.read_object_end()
    body.append(_expr_stmt(_method_call(reader, "read_object_end")))

    # Assert required fields were seen (triggers narrowing for constructor)
    for req_name in required_fields:
        body.append(TpyAssert(
            condition=TpyBinOp(
                left=_name(req_name), op="is not", right=TpyNoneLiteral(),
            ),
            message=_str_lit(f"json: missing required field '{req_name}'"),
        ))

    # return ClassName(field1, field2, ...)
    body.append(TpyReturn(
        value=TpyCall(func=cls.name, args=[_name(f.name) for f in all_fields]),
    ))

    cls_type = NamedType(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else OwnType(cls_type)
    return TpyFunction(
        name="_from_reader",
        params=[("__reader", reader_type)],
        return_type=ret_type,
        body=body,
        is_method=True,
        is_staticmethod=True,
    )


def _build_field_dispatch(fields: list[FieldInfo], reader: TpyExpr) -> TpyMatch:
    """Build match/case dispatch for field name matching."""
    key = _name("__key")
    cases: list[TpyMatchCase] = []

    for fld in fields:
        body = list(_build_read_value_stmts(fld.name, fld.type, reader))
        cases.append(TpyMatchCase(
            pattern=TpyLiteralPattern(value=fld.name),
            guard=None,
            body=body,
        ))

    # Default case: skip unknown fields
    cases.append(TpyMatchCase(
        pattern=TpyWildcardPattern(),
        guard=None,
        body=[_expr_stmt(_method_call(reader, "skip_value"))],
    ))

    return TpyMatch(subject=key, cases=cases)


def _build_from_json(cls: ClassInfo) -> TpyFunction:
    """Build from_json(s: str) -> Self static method (public entry point)."""
    cls_type = NamedType(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else OwnType(cls_type)
    body: list[TpyStmt] = [
        TpyVarDecl(
            name="__reader", type=NamedType("JsonReader"),
            init=TpyCall(func="JsonReader", args=[_name("__s")]),
        ),
        TpyReturn(
            value=_method_call(_name(cls.name), "_from_reader", [_name("__reader")]),
        ),
    ]
    return TpyFunction(
        name="from_json",
        params=[("__s", StrType())],
        return_type=ret_type,
        body=body,
        is_method=True,
        is_staticmethod=True,
    )


# ---------------------------------------------------------------------------
# to_json / _to_writer body generation
# ---------------------------------------------------------------------------

def _build_write_value_stmts(
    access: TpyExpr, fld_type: TypeInfo, writer: TpyExpr,
) -> list[TpyStmt]:
    """Build statements to write a single value to the writer."""
    inner = _unwrap_optional(fld_type)
    if inner is not None:
        # Assign to a local var, check "is not None", then assign the
        # narrowed value to a typed local to work around a narrowing
        # limitation with method call arguments.
        if isinstance(access, TpyFieldAccess):
            opt_var = f"__opt_{access.field}"
            val_var = f"__val_{access.field}"
        else:
            opt_var = "__opt_val"
            val_var = "__unwrap_val"
        inner_type = inner._tpy_type
        then_body: list[TpyStmt] = [
            TpyVarDecl(name=val_var, type=inner_type, init=_name(opt_var)),
        ]
        then_body.extend(_build_write_value_stmts(_name(val_var), inner, writer))
        return [
            TpyVarDecl(name=opt_var, type=None, init=access),
            TpyIf(
                condition=TpyBinOp(left=_name(opt_var), op="is not", right=TpyNoneLiteral()),
                then_body=then_body,
                else_body=[_expr_stmt(_method_call(writer, "write_null"))],
            ),
        ]

    if _is_list_type(fld_type) and fld_type.type_args:
        elem_type = fld_type.type_args[0]

        return [
            _expr_stmt(_method_call(writer, "array_start")),
            TpyForEach(
                var="__item", iterable=access,
                body=_build_write_value_stmts(_name("__item"), elem_type, writer),
            ),
            _expr_stmt(_method_call(writer, "array_end")),
        ]

    # dict[str, V] -- iterate items() to avoid double lookup
    if _is_dict_type(fld_type) and fld_type.type_args and len(fld_type.type_args) == 2:
        val_type = fld_type.type_args[1]
        synth_var = _fresh_tmp("kv")
        loop_body: list[TpyStmt] = [
            TpyTupleUnpack(targets=["__dk", "__dv"], value=_name(synth_var)),
            _expr_stmt(_method_call(writer, "key", [_name("__dk")])),
        ]
        loop_body.extend(_build_write_value_stmts(_name("__dv"), val_type, writer))
        return [
            _expr_stmt(_method_call(writer, "object_start")),
            TpyForEach(
                var=synth_var,
                iterable=_method_call(access, "items"),
                body=loop_body,
                is_tuple_unpack=True,
            ),
            _expr_stmt(_method_call(writer, "object_end")),
        ]

    # tuple[T1, T2, ...]: write as JSON array
    if _is_tuple_type(fld_type):

        elem_types = [TypeInfo.from_tpy_type(et) for et in fld_type._tpy_type.element_types]
        stmts: list[TpyStmt] = [_expr_stmt(_method_call(writer, "array_start"))]
        for i, et in enumerate(elem_types):
            elem_access = TpySubscript(obj=access, index=TpyIntLiteral(value=i))
            stmts.extend(_build_write_value_stmts(elem_access, et, writer))
        stmts.append(_expr_stmt(_method_call(writer, "array_end")))
        return stmts

    if _is_enum_type(fld_type):
        return [_expr_stmt(_method_call(
            writer, "write_str",
            [TpyFieldAccess(obj=access, field="name")],
        ))]

    if _is_model_record(fld_type):
        return [_expr_stmt(_method_call(access, "_to_writer", [writer]))]

    if _is_bigint_type(fld_type):
        # Write BigInt as JSON string to preserve precision
        return [_expr_stmt(_method_call(writer, "write_str", [_call("str", [access])]))]

    # Primitive write
    write_method = _get_write_method(fld_type)
    return [_expr_stmt(_method_call(writer, write_method, [access]))]


def _get_write_method(fld_type: TypeInfo) -> str:
    if _is_str_type(fld_type):
        return "write_str"
    if _is_bool_type(fld_type):
        return "write_bool"
    if isinstance(fld_type._tpy_type, Float32Type):
        return "write_float32"
    if _is_float_type(fld_type):
        return "write_float"
    if isinstance(fld_type._tpy_type, FixedIntType) and fld_type._tpy_type.bits == 32 and fld_type._tpy_type.signed:
        return "write_int32"
    if _is_int_type(fld_type):
        return "write_int"
    raise MacroError(f"@model: unsupported field type for serialization '{fld_type.name}'")


def _build_to_writer(cls: ClassInfo, all_fields: list[FieldInfo]) -> TpyFunction:
    """Build _to_writer(self, writer: JsonWriter) -> None method."""
    writer = _writer_name()
    writer_type = NamedType("JsonWriter")
    body: list[TpyStmt] = []

    # __writer.object_start()
    body.append(_expr_stmt(_method_call(writer, "object_start")))

    for fld in all_fields:
        # __writer.key("field_name")
        body.append(_expr_stmt(_method_call(writer, "key", [_str_lit(fld.name)])))
        access = TpyFieldAccess(obj=_name("self"), field=fld.name)
        body.extend(_build_write_value_stmts(access, fld.type, writer))

    # __writer.object_end()
    body.append(_expr_stmt(_method_call(writer, "object_end")))

    return TpyFunction(
        name="_to_writer",
        params=[("__writer", writer_type)],
        return_type=VoidType(),
        body=body,
        is_method=True,
        readonly_opt_out=True,
    )


def _build_to_json(cls: ClassInfo) -> TpyFunction:
    """Build to_json(self) -> str method (public entry point)."""
    # def to_json(self) -> str:
    #     __writer = JsonWriter()
    #     self._to_writer(__writer)
    #     return __writer.finish()
    writer_type = NamedType("JsonWriter")
    body: list[TpyStmt] = [
        TpyVarDecl(
            name="__writer", type=writer_type,
            init=TpyCall(func="JsonWriter", args=[]),
        ),
        _expr_stmt(_method_call(_name("self"), "_to_writer", [_name("__writer")])),
        TpyReturn(value=_method_call(_name("__writer"), "finish")),
    ]
    return TpyFunction(
        name="to_json",
        params=[],
        return_type=StrType(),
        body=body,
        is_method=True,
        readonly_opt_out=True,
    )


# ---------------------------------------------------------------------------
# @model class macro
# ---------------------------------------------------------------------------

@class_macro
def model(cls: ClassInfo, *, frozen: bool = False, order: bool = False) -> None:
    """Transform a class into a JSON-serializable model with @dataclass behavior."""
    # Apply @dataclass behavior
    cls.is_dataclass = True
    cls.is_frozen = frozen
    cls.is_ordered = order

    parent_fields = cls.get_parent_fields()
    all_fields = parent_fields + cls.fields

    if not all_fields:
        raise MacroError(f"@model class '{cls.name}' must have at least one field")

    # Validate field order (no non-default after default)
    seen_default = False
    for fld in all_fields:
        if fld.has_default:
            seen_default = True
        elif seen_default:
            raise MacroError(
                f"Field '{fld.name}' without default follows field with default "
                f"in @model class '{cls.name}'",
                loc=fld.loc,
            )

    # Generate __init__
    if not cls.has_method("__init__"):
        init_fn = build_init(cls, parent_fields, cls.fields)
        cls.add_method(init_fn)

    # Generate __eq__
    if not cls.has_method("__eq__"):
        eq_fn = build_eq(cls, all_fields)
        cls.add_method(eq_fn)

    # Generate __repr__ stub
    if not cls.has_method("__repr__"):
        cls.add_method_stub("__repr__", [], StrType(), is_readonly=True)

    if frozen and not cls.has_method("__hash__"):
        cls.add_method_stub("__hash__", [], UINT64, is_readonly=True)

    cls.set_dataclass_fields(all_fields)

    # Discover enum types among fields so builders can detect them
    global _tmp_counter
    _tmp_counter = 0

    # Generate JSON methods
    cls.add_method(_build_from_reader(cls, all_fields))
    cls.add_method(_build_from_json(cls))
    cls.add_method(_build_to_writer(cls, all_fields))
    cls.add_method(_build_to_json(cls))
