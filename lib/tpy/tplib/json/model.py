# tpy: macro_module
"""JSON model macro for TurboPython.

Provides @model class macro that bundles @dataclass behavior with
JSON serialization/deserialization via tplib.json.JsonReader/JsonWriter.

Usage:

    from tplib.json.model import model

    @model
    class User:
        name: str
        age: Int32
        score: float = 0.0

    u = User("Alice", 30, 9.5)
    s = u.to_json()                  # '{"name": "Alice", "age": 30, "score": 9.5}'
    u2 = User.from_json(s)           # panics on parse error
    u3 = User.try_from_json(s)       # raises JsonError on parse error

Options:

    @model(frozen=True)   -- immutable fields, generates __hash__
    @model(order=True)    -- generates comparison operators

Generated methods:
  - __init__, __eq__, __repr__
  - to_json() -> str                          -- serialize to JSON string
  - from_json(s: str) -> Self                 -- deserialize, panics on error
  - try_from_json(s: str) -> Self             -- deserialize with @error_return(JsonError)

Supported field types:
  - Primitives: str, bool, int/Int32/Int64/BigInt, float/Float32
  - Containers: list[T], dict[str, V], tuple[T, ...]
  - Enum types
  - Optional[T]
  - Nested @model records
  - User-defined types implementing __json_encode__/__json_decode__

User-defined type protocol -- any class with these two methods can be
used as a field in @model classes:

    class Seconds:
        _value: Int32
        def __json_encode__(self, writer: JsonWriter) -> None:
            writer.write_int32(self._value)
        @staticmethod
        @error_return(JsonError)
        def __json_decode__(reader: JsonReader) -> Own[Seconds]:
            raw = reader.read_int()
            return Seconds(Int32(raw))

    @model
    class Event:
        name: str
        when: Seconds  # works as @model field
"""

from tpyc.macro_api import (
    ClassInfo, FieldInfo, TypeInfo, MacroError,
    class_macro, macro_deps,
    build_init, build_eq,
    ast, types, Expr, Stmt, Function, Type,
)

macro_deps(
    "tplib.json.parser",
    "tplib.json.writer",
    ("tpy", "try_parse"),
)


_JSON_ERROR = "tplib.json.parser.JsonError"


def _raise_if(condition: Expr, message: str) -> Stmt:
    """Build: if condition: raise JsonError(message)"""
    return ast.if_(condition, [ast.raise_(_JSON_ERROR, [ast.str_lit(message)])])


# ---------------------------------------------------------------------------
# from_json / __json_decode__ body generation
# ---------------------------------------------------------------------------

def _build_read_into(fld_type: TypeInfo, reader: Expr, hint: str = "v") -> tuple[list[Stmt], str]:
    """Read any JSON value into a fresh temp variable.

    Returns (statements, var_name). Works for all supported types
    including containers and enums.
    """
    # Simple types: reader call as standalone statement
    tmp = ast.fresh_tmp(hint)
    stmts = _try_build_read_stmts(fld_type, reader, tmp)
    if stmts is not None:
        return (stmts, tmp)

    # Model record: __json_decode__ call as standalone VarDecl
    if fld_type.is_record:
        return ([ast.var_decl(tmp, init=ast.method_call(
            ast.name(fld_type.name), "__json_decode__", [reader]))], tmp)

    # Enum: read_str first (auto-propagates), then try_parse + raise
    if fld_type.is_enum:
        raw_str = ast.fresh_tmp("estr")
        parsed = ast.fresh_tmp("parsed")
        enum_name = fld_type.enum_name
        return ([
            ast.var_decl(raw_str, init=ast.method_call(reader, "read_str")),
            ast.var_decl(parsed, init=ast.call("try_parse",
                         [ast.name(enum_name), ast.name(raw_str)])),
            _raise_if(ast.binop(ast.name(parsed), "is", ast.none_lit()),
                      f"invalid enum value for '{enum_name}'"),
            ast.var_decl(tmp, type=fld_type.raw_type, init=ast.name(parsed)),
        ], tmp)

    # Container types: declare with default, read in-place
    tmp = ast.fresh_tmp(hint)
    default = _default_for_type(fld_type)
    stmts: list[Stmt] = [ast.var_decl(tmp, type=fld_type.raw_type, init=default)]
    stmts.extend(_build_read_value_stmts(tmp, fld_type, reader))
    return (stmts, tmp)


def _try_build_read_stmts(fld_type: TypeInfo, reader: Expr, var_name: str) -> list[Stmt] | None:
    """Try to build statements that read a value into var_name.

    Returns None for types that need container/tuple/enum handling.
    Reader calls are always standalone statements so @error_return
    auto-propagation works correctly.
    """
    if fld_type.is_str:
        return [ast.var_decl(var_name, init=ast.method_call(reader, "read_str"))]
    if fld_type.is_bool:
        return [ast.var_decl(var_name, init=ast.method_call(reader, "read_bool"))]
    if fld_type.is_float:
        if fld_type.is_float32:
            raw = ast.fresh_tmp("raw")
            return [
                ast.var_decl(raw, init=ast.method_call(reader, "read_float")),
                ast.var_decl(var_name, init=ast.call("Float32", [ast.name(raw)])),
            ]
        return [ast.var_decl(var_name, init=ast.method_call(reader, "read_float"))]
    if fld_type.is_int:
        type_name = fld_type.int_type_name
        if type_name != "Int64":
            raw = ast.fresh_tmp("raw")
            return [
                ast.var_decl(raw, init=ast.method_call(reader, "read_int")),
                ast.var_decl(var_name, init=ast.call(type_name, [ast.name(raw)])),
            ]
        return [ast.var_decl(var_name, init=ast.method_call(reader, "read_int"))]
    if fld_type.is_bigint:
        raw = ast.fresh_tmp("raw")
        return [
            ast.var_decl(raw, init=ast.method_call(reader, "read_str")),
            ast.var_decl(var_name, init=ast.call("int", [ast.name(raw)])),
        ]
    # Model records and other unhandled types: handled by _build_read_into.
    return None


def _default_for_type(ti: TypeInfo) -> Expr:
    """Build a default initializer expression for a type."""
    if ti.is_str:
        return ast.str_lit("")
    if ti.is_bool:
        return ast.bool_lit(False)
    if ti.is_int or ti.is_bigint:
        return ast.int_lit(0)
    if ti.is_float:
        return ast.float_lit(0.0)
    if ti.is_list:
        return ast.list_lit()
    if ti.is_dict:
        return ast.dict_lit()
    if ti.unwrap_optional() is not None:
        return ast.none_lit()
    raise MacroError(f"@model: no default for type '{ti.name}'")


def _build_read_value_stmts(
    fld_name: str, fld_type: TypeInfo, reader: Expr,
) -> list[Stmt]:
    """Build statements to read a field value and assign to local var.

    Handles all types: Optional, list, dict, tuple, enum, primitives, models.
    """
    target = ast.name(fld_name)

    # Optional[T]: check for null, then read inner
    inner = fld_type.unwrap_optional()
    if inner is not None:
        peek = ast.method_call(reader, "peek")
        none_token = ast.field_access(ast.name("JsonToken"), "NONE")
        read_null = ast.expr_stmt(ast.method_call(reader, "read_null"))
        else_body = _read_and_assign(fld_name, inner, reader)
        return [ast.if_(
            ast.binop(peek, "==", none_token),
            then_body=[read_null],
            else_body=else_body,
        )]

    # list[T]: read array, append each element
    if fld_type.is_list and fld_type.type_args:
        elem_type = fld_type.type_args[0]
        loop_body = _read_and_append(target, elem_type, reader)
        return [
            ast.expr_stmt(ast.method_call(reader, "read_array_start")),
            ast.while_(ast.method_call(reader, "has_next"), loop_body),
            ast.expr_stmt(ast.method_call(reader, "read_array_end")),
        ]

    # dict[str, V]: read object, set each key-value
    if fld_type.is_dict and fld_type.type_args and len(fld_type.type_args) == 2:
        val_type = fld_type.type_args[1]
        dk_var = ast.fresh_tmp("dk")
        loop_body: list[Stmt] = [
            ast.var_decl(dk_var, type=types.str,
                         init=ast.method_call(reader, "read_key")),
        ]
        loop_body.extend(_read_and_assign_subscript(target, dk_var, val_type, reader))
        return [
            ast.expr_stmt(ast.method_call(reader, "read_object_start")),
            ast.while_(ast.method_call(reader, "has_next"), loop_body),
            ast.expr_stmt(ast.method_call(reader, "read_object_end")),
        ]

    # tuple[T1, T2, ...]: read as JSON array, positional
    if fld_type.is_tuple:
        elem_types = fld_type.tuple_element_types
        stmts: list[Stmt] = [ast.expr_stmt(ast.method_call(reader, "read_array_start"))]
        elem_vars: list[str] = []
        for i, et in enumerate(elem_types):
            es, ev = _build_read_into(et, reader, f"t{i}")
            stmts.extend(es)
            elem_vars.append(ev)
            if i < len(elem_types) - 1:
                stmts.append(_raise_if(
                    ast.binop(ast.method_call(reader, "has_next"),
                              "==", ast.bool_lit(False)),
                    "tuple: not enough elements",
                ))
        stmts.append(ast.expr_stmt(ast.method_call(reader, "read_array_end")))
        stmts.append(ast.assign(
            target, ast.tuple_lit([ast.name(v) for v in elem_vars]),
        ))
        return stmts

    # Simple types: direct assignment without temp var
    return _read_and_assign(fld_name, fld_type, reader)


def _read_and_assign(fld_name: str, fld_type: TypeInfo, reader: Expr) -> list[Stmt]:
    """Read a value and assign to fld_name."""
    # Model records: assign directly from __json_decode__() call (rvalue)
    # to avoid copy warning when target is Optional[Own[T]].
    if fld_type.is_record:
        return [ast.assign(
            ast.name(fld_name),
            ast.method_call(ast.name(fld_type.name), "__json_decode__", [reader]),
        )]
    stmts, var = _build_read_into(fld_type, reader, fld_name)
    stmts.append(ast.assign(ast.name(fld_name), ast.name(var)))
    return stmts


def _read_and_append(target: Expr, elem_type: TypeInfo, reader: Expr) -> list[Stmt]:
    """Read one element and append to target via temp variable."""
    stmts, var = _build_read_into(elem_type, reader, "elem")
    stmts.append(ast.expr_stmt(ast.method_call(target, "append", [ast.name(var)])))
    return stmts


def _read_and_assign_subscript(
    target: Expr, key_var: str, val_type: TypeInfo, reader: Expr,
) -> list[Stmt]:
    """Read one value and assign to target[key_var] via temp variable."""
    stmts, var = _build_read_into(val_type, reader, "dv")
    stmts.append(ast.assign(
        ast.subscript(target, ast.name(key_var)),
        ast.name(var),
    ))
    return stmts


def _build_field_default(fld: FieldInfo) -> Expr | None:
    """Build the default initializer for a local variable before parsing.

    Returns None for types that have no sensible zero value (model records,
    enums, tuples) -- these are required fields.
    """
    if fld.has_default and fld.default_expr is not None:
        return fld.default_expr
    ti = fld.type
    if ti.is_record or ti.is_enum or ti.is_tuple:
        return None
    try:
        return _default_for_type(ti)
    except MacroError:
        return None


def _build_json_decode(cls: ClassInfo, all_fields: list[FieldInfo]) -> Function:
    """Build __json_decode__(reader: JsonReader) -> Self static method."""
    reader = ast.name("__reader")
    reader_type = types.named("JsonReader")
    body: list[Stmt] = []

    # __reader.read_object_start()
    body.append(ast.expr_stmt(ast.method_call(reader, "read_object_start")))

    # Declare local vars for each field with defaults.
    # Fields with no zero value (models, enums, tuples) are declared as
    # Optional and asserted non-None after parsing.
    required_fields: list[str] = []
    for fld in all_fields:
        default = _build_field_default(fld)
        if default is not None:
            # For Optional[NonValueType] fields, use Optional[Own[T]] so the
            # C++ repr is std::optional<T> (value) rather than T* (pointer).
            fld_raw = fld.type.raw_type
            inner_opt = fld.type.unwrap_optional()
            if inner_opt is not None and not inner_opt.is_value_type:
                fld_raw = types.optional(types.own(inner_opt.raw_type))
            body.append(ast.var_decl(fld.name, type=fld_raw, init=default))
        elif fld.type.is_record or fld.type.is_enum or fld.type.is_tuple:
            # Use Optional[Own[T]] for non-value types so the C++ repr is
            # std::optional<T> (value semantics) rather than T* (pointer repr).
            inner = fld.type.raw_type
            if not fld.type.is_value_type:
                inner = types.own(inner)
            body.append(ast.var_decl(
                fld.name, type=types.optional(inner), init=ast.none_lit(),
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
        loop_body: list[Stmt] = [
            ast.var_decl(key_var, type=types.str_view,
                         init=ast.method_call(reader, "read_key_raw")),
            dispatch,
        ]
        body.append(ast.while_(
            ast.method_call(reader, "has_next"), loop_body,
        ))

    # __reader.read_object_end()
    body.append(ast.expr_stmt(ast.method_call(reader, "read_object_end")))

    # Raise if required fields were not seen (triggers narrowing for constructor)
    for req_name in required_fields:
        body.append(_raise_if(
            ast.binop(ast.name(req_name), "is", ast.none_lit()),
            f"missing required field '{req_name}'",
        ))

    # return ClassName(field1, field2, ...)
    body.append(ast.return_(
        ast.call(cls.name, [ast.name(f.name) for f in all_fields]),
    ))

    cls_type = types.named(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else types.own(cls_type)
    return ast.function(
        "__json_decode__", [("__reader", reader_type)], ret_type, body,
        is_method=True, is_staticmethod=True, error_return=_JSON_ERROR,
    )


def _build_field_dispatch(fields: list[FieldInfo], reader: Expr) -> Stmt:
    """Build match/case dispatch for field name matching."""
    key = ast.name("__key")
    cases = []

    for fld in fields:
        body = list(_build_read_value_stmts(fld.name, fld.type, reader))
        cases.append(ast.match_case(ast.literal_pattern(fld.name), body))

    # Default case: skip unknown fields
    cases.append(ast.match_case(
        ast.wildcard_pattern(),
        [ast.expr_stmt(ast.method_call(reader, "skip_value"))],
    ))

    return ast.match(key, cases)


def _build_from_json(cls: ClassInfo) -> Function:
    """Build from_json(s: str) -> Self static method (panics on error).

    Wraps __json_decode__ in try/except and panics on parse errors.
    For error-handling version, use try_from_json.
    """
    cls_type = types.named(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else types.own(cls_type)
    # Use Optional[Own[T]] for the result variable to avoid pointer-repr
    # issues and ensure the value is definitely available after try/except.
    inner = cls_type
    if not cls_type.is_value_type():
        inner = types.own(cls_type)
    result_var = "__result"
    body: list[Stmt] = [
        ast.var_decl("__reader", type=types.named("JsonReader"),
                      init=ast.call("JsonReader", [ast.name("__s")])),
        ast.var_decl(result_var, type=types.optional(inner),
                      init=ast.none_lit()),
        ast.try_(
            try_body=[
                ast.assign(ast.name(result_var),
                           ast.method_call(ast.name(cls.name), "__json_decode__",
                                           [ast.name("__reader")])),
            ],
            handlers=[ast.except_handler(
                _JSON_ERROR, body=[
                    ast.assert_(ast.bool_lit(False), ast.str_lit("json: parse error")),
                ],
            )],
        ),
        ast.assert_(
            ast.binop(ast.name(result_var), "is not", ast.none_lit()),
            ast.str_lit("json: unreachable"),
        ),
        ast.return_(ast.name(result_var)),
    ]
    return ast.function(
        "from_json", [("__s", types.str)], ret_type, body,
        is_method=True, is_staticmethod=True,
    )


def _build_try_from_json(cls: ClassInfo) -> Function:
    """Build try_from_json(s: str) -> Self with @error_return(JsonError).

    Calls __json_decode__ with auto-propagation. Callers handle with
    try/except JsonError.
    """
    cls_type = types.named(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else types.own(cls_type)
    body: list[Stmt] = [
        ast.var_decl("__reader", type=types.named("JsonReader"),
                      init=ast.call("JsonReader", [ast.name("__s")])),
        ast.return_(
            ast.method_call(ast.name(cls.name), "__json_decode__", [ast.name("__reader")]),
        ),
    ]
    return ast.function(
        "try_from_json", [("__s", types.str)], ret_type, body,
        is_method=True, is_staticmethod=True, error_return=_JSON_ERROR,
    )


# ---------------------------------------------------------------------------
# to_json / __json_encode__ body generation
# ---------------------------------------------------------------------------

def _build_write_value_stmts(
    access: Expr, fld_type: TypeInfo, writer: Expr,
) -> list[Stmt]:
    """Build statements to write a single value to the writer."""
    inner = fld_type.unwrap_optional()
    if inner is not None:
        # Assign to a local var, check "is not None", then assign the
        # narrowed value to a typed local to work around a narrowing
        # limitation with method call arguments.
        field_name = ast.get_field_name(access)
        if field_name is not None:
            opt_var = f"__opt_{field_name}"
            val_var = f"__val_{field_name}"
        else:
            opt_var = "__opt_val"
            val_var = "__unwrap_val"
        inner_raw = inner.raw_type
        then_body: list[Stmt] = [
            ast.var_decl(val_var, type=inner_raw, init=ast.name(opt_var)),
        ]
        then_body.extend(_build_write_value_stmts(ast.name(val_var), inner, writer))
        return [
            ast.var_decl(opt_var, init=access),
            ast.if_(
                ast.binop(ast.name(opt_var), "is not", ast.none_lit()),
                then_body=then_body,
                else_body=[ast.expr_stmt(ast.method_call(writer, "write_null"))],
            ),
        ]

    if fld_type.is_list and fld_type.type_args:
        elem_type = fld_type.type_args[0]
        return [
            ast.expr_stmt(ast.method_call(writer, "array_start")),
            ast.for_each("__item", access,
                         _build_write_value_stmts(ast.name("__item"), elem_type, writer)),
            ast.expr_stmt(ast.method_call(writer, "array_end")),
        ]

    # dict[str, V] -- iterate items() to avoid double lookup
    if fld_type.is_dict and fld_type.type_args and len(fld_type.type_args) == 2:
        val_type = fld_type.type_args[1]
        synth_var = ast.fresh_tmp("kv")
        loop_body: list[Stmt] = [
            ast.tuple_unpack(["__dk", "__dv"], ast.name(synth_var)),
            ast.expr_stmt(ast.method_call(writer, "key", [ast.name("__dk")])),
        ]
        loop_body.extend(_build_write_value_stmts(ast.name("__dv"), val_type, writer))
        return [
            ast.expr_stmt(ast.method_call(writer, "object_start")),
            ast.for_each(synth_var, ast.method_call(access, "items"),
                         loop_body, is_tuple_unpack=True),
            ast.expr_stmt(ast.method_call(writer, "object_end")),
        ]

    # tuple[T1, T2, ...]: write as JSON array
    if fld_type.is_tuple:
        elem_types = fld_type.tuple_element_types
        stmts: list[Stmt] = [ast.expr_stmt(ast.method_call(writer, "array_start"))]
        for i, et in enumerate(elem_types):
            elem_access = ast.subscript(access, ast.int_lit(i))
            stmts.extend(_build_write_value_stmts(elem_access, et, writer))
        stmts.append(ast.expr_stmt(ast.method_call(writer, "array_end")))
        return stmts

    if fld_type.is_enum:
        return [ast.expr_stmt(ast.method_call(
            writer, "write_str",
            [ast.field_access(access, "name")],
        ))]

    if fld_type.is_record:
        return [ast.expr_stmt(ast.method_call(access, "__json_encode__", [writer]))]

    if fld_type.is_bigint:
        # Write BigInt as JSON string to preserve precision
        return [ast.expr_stmt(ast.method_call(
            writer, "write_str", [ast.call("str", [access])]))]

    # Primitive write
    write_method = _get_write_method(fld_type)
    return [ast.expr_stmt(ast.method_call(writer, write_method, [access]))]


def _get_write_method(fld_type: TypeInfo) -> str:
    if fld_type.is_str:
        return "write_str"
    if fld_type.is_bool:
        return "write_bool"
    if fld_type.is_float32:
        return "write_float32"
    if fld_type.is_float:
        return "write_float"
    if fld_type.is_int32:
        return "write_int32"
    if fld_type.is_int:
        return "write_int"
    raise MacroError(f"@model: unsupported field type for serialization '{fld_type.name}'")


def _build_json_encode(cls: ClassInfo, all_fields: list[FieldInfo]) -> Function:
    """Build __json_encode__(self, writer: JsonWriter) -> None method."""
    writer = ast.name("__writer")
    writer_type = types.named("JsonWriter")
    body: list[Stmt] = []

    # __writer.object_start()
    body.append(ast.expr_stmt(ast.method_call(writer, "object_start")))

    for fld in all_fields:
        # __writer.key("field_name")
        body.append(ast.expr_stmt(ast.method_call(writer, "key", [ast.str_lit(fld.name)])))
        access = ast.field_access(ast.name("self"), fld.name)
        body.extend(_build_write_value_stmts(access, fld.type, writer))

    # __writer.object_end()
    body.append(ast.expr_stmt(ast.method_call(writer, "object_end")))

    return ast.function(
        "__json_encode__", [("__writer", writer_type)], types.void, body,
        is_method=True,
    )


def _build_to_json(cls: ClassInfo) -> Function:
    """Build to_json(self, indent: Int32 = 0) -> str method (public entry point)."""
    writer_type = types.named("JsonWriter")
    body: list[Stmt] = [
        ast.var_decl("__writer", type=writer_type,
                      init=ast.call("JsonWriter", [ast.name("indent")])),
        ast.expr_stmt(ast.method_call(ast.name("self"), "__json_encode__", [ast.name("__writer")])),
        ast.return_(ast.method_call(ast.name("__writer"), "finish")),
    ]
    return ast.function(
        "to_json", [("indent", types.int32)], types.str, body,
        is_method=True, defaults=[ast.int_lit(0)],
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
        cls.add_method_stub("__repr__", [], types.str, is_readonly=True)

    if frozen and not cls.has_method("__hash__"):
        cls.add_method_stub("__hash__", [], types.uint64, is_readonly=True)

    cls.set_dataclass_fields(all_fields)

    # Generate JSON methods
    has_parent = len(parent_fields) > 0
    for fn in [
        _build_json_decode(cls, all_fields),
        _build_from_json(cls),
        _build_try_from_json(cls),
        _build_json_encode(cls, all_fields),
        _build_to_json(cls),
    ]:
        fn.hides_parent = has_parent
        cls.add_method(fn)
