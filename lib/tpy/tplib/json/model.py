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

Field renaming:

    from tplib.json.model import model, field

    @model
    class User:
        first_name: str = field(alias="firstName")

    User("Alice").to_json()       # '{"firstName": "Alice"}'

Generated methods:
  - __init__, __eq__, __repr__
  - to_json() -> str                          -- serialize to JSON string
  - from_json(s: str) -> Self                 -- deserialize, panics on error
  - try_from_json(s: str) -> Self             -- deserialize with @error_return(JsonError)
  - save_json(path: str, indent: Int32 = 0) -> None  -- serialize and write to file
  - load_json(path: str) -> Self              -- read file and deserialize, panics on error
  - try_load_json(path: str) -> Self          -- read file and deserialize with @error_return

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
    expr_to_cpp_default,
    ast, types, Expr, Stmt, Function, Type,
)
from _macro_helpers import (
    ORDER_DUNDERS, build_init, build_eq, build_repr, build_hash, build_order,
)

macro_deps(
    "tplib.json.parser",
    "tplib.json.writer",
    ("tpy", "try_parse"),
)


_JSON_ERROR = "tplib.json.parser.JsonError"

_MISSING = object()


class Field:
    """Descriptor returned by field(). Inspected by @model macro."""

    def __init__(self, *, alias=_MISSING, default=_MISSING, default_factory=_MISSING):
        if default is not _MISSING and default_factory is not _MISSING:
            raise TypeError("cannot specify both 'default' and 'default_factory'")
        self.alias = None if alias is _MISSING else alias
        self.default = default
        self.default_factory = default_factory


def field(*, alias=_MISSING, default=_MISSING, default_factory=_MISSING) -> Field:
    """Declare field metadata in @model classes.

    alias: alternate name used as JSON key (Python field name unchanged).
    default / default_factory: same as dataclasses.field().
    """
    return Field(alias=alias, default=default, default_factory=default_factory)


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

    # Enum: read_str_raw (zero-copy StrView), then try_parse + raise
    if fld_type.is_enum:
        raw_str = ast.fresh_tmp("estr")
        parsed = ast.fresh_tmp("parsed")
        enum_name = fld_type.enum_name
        return ([
            ast.var_decl(raw_str, init=ast.method_call(reader, "read_str_raw")),
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
        # BigInt round-trips as a bare JSON number (read_number_raw preserves
        # arbitrary precision), matching stdlib json.dumps/loads and CPython.
        raw = ast.fresh_tmp("raw")
        return [
            ast.var_decl(raw, init=ast.method_call(reader, "read_number_raw")),
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
            # Plain `T | None`: Own is rejected on a local (it moves out at
            # last use into the constructor call regardless).
            body.append(ast.var_decl(fld.name, type=fld.type.raw_type, init=default))
        elif fld.type.is_record or fld.type.is_enum or fld.type.is_tuple:
            body.append(ast.var_decl(
                fld.name, type=types.optional(fld.type.raw_type), init=ast.none_lit(),
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
        dispatch = _build_field_dispatch(cls.name, all_fields, reader)
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


def _build_field_dispatch(cls_name: str, fields: list[FieldInfo], reader: Expr) -> Stmt:
    """Build match/case dispatch for field name matching."""
    key = ast.name("__key")
    cases = []

    for fld in fields:
        body = list(_build_read_value_stmts(fld.name, fld.type, reader))
        cases.append(ast.match_case(ast.literal_pattern(_json_key(cls_name, fld)), body))

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
    # Plain `T | None` result local (Own is rejected on a local); it moves out
    # on the `Own[T] | None` return.
    inner = cls_type
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
                _JSON_ERROR, binding="__e", body=[
                    ast.assert_(ast.bool_lit(False),
                                ast.field_access(ast.name("__e"), "message")),
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
        return [ast.expr_stmt(ast.method_call(writer, "write_bigint", [access]))]

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
        body.append(ast.expr_stmt(ast.method_call(writer, "key", [ast.str_lit(_json_key(cls.name, fld))])))
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


def _build_save_json(cls: ClassInfo) -> Function:
    """Build save_json(self, path: str, indent: Int32 = 0) -> None."""
    body: list[Stmt] = ast.quote("""
        with open(__path, "w") as __f:
            __f.write(self.to_json(indent))
    """)
    return ast.function(
        "save_json", [("__path", types.str), ("indent", types.int32)],
        types.void, body,
        is_method=True, defaults=[None, ast.int_lit(0)],
    )


def _build_load_json(cls: ClassInfo) -> Function:
    """Build load_json(path: str) -> Self static method (panics on error)."""
    cls_type = types.named(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else types.own(cls_type)
    body: list[Stmt] = ast.quote(f"""
        with open(__path, "r") as __f:
            __data: str = __f.read()
        return {cls.name}.from_json(__data)
    """)
    return ast.function(
        "load_json", [("__path", types.str)], ret_type, body,
        is_method=True, is_staticmethod=True,
    )


def _build_try_load_json(cls: ClassInfo) -> Function:
    """Build try_load_json(path: str) -> Self with @error_return(JsonError)."""
    cls_type = types.named(cls.name)
    ret_type = cls_type if cls_type.is_value_type() else types.own(cls_type)
    body: list[Stmt] = ast.quote(f"""
        with open(__path, "r") as __f:
            __data: str = __f.read()
        return {cls.name}.try_from_json(__data)
    """)
    return ast.function(
        "try_load_json", [("__path", types.str)], ret_type, body,
        is_method=True, is_staticmethod=True, error_return=_JSON_ERROR,
    )


# ---------------------------------------------------------------------------
# @model class macro
# ---------------------------------------------------------------------------

# class_name -> {field_name: json_key} for alias persistence across inheritance
_aliases: dict[str, dict[str, str]] = {}

# Tracks which records were built by @model (for parent field inheritance)
_model_records: set[str] = set()


def _json_key(cls_name: str, fld: FieldInfo) -> str:
    """Get the JSON key for a field (alias if set, otherwise field name)."""
    return _aliases.get(cls_name, {}).get(fld.name, fld.name)


def _process_fields(cls: ClassInfo) -> None:
    """Unwrap Field descriptors set by field() calls."""
    for fld in cls.fields:
        if not isinstance(fld.default_obj, Field):
            continue
        spec = fld.default_obj
        if spec.alias is not None:
            if not isinstance(spec.alias, str):
                raise MacroError(
                    f"field alias must be a string literal",
                    loc=fld.loc,
                )
            if cls.name not in _aliases:
                _aliases[cls.name] = {}
            _aliases[cls.name][fld.name] = spec.alias
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
            factory_call.loc = fld.loc
            fld.set_default(factory_call, is_factory=True)
        else:
            fld.clear_default()


@class_macro
def model(cls: ClassInfo, *, frozen: bool = False, order: bool = False) -> None:
    """Transform a class into a JSON-serializable model with @dataclass behavior."""
    # Apply @dataclass behavior
    cls.is_frozen = frozen

    _process_fields(cls)

    # Only inherit fields from parent @model records
    parent_name = cls.parent.name if cls.parent else None
    if parent_name and parent_name in _model_records:
        parent_fields = cls.get_parent_fields()
    else:
        parent_fields = []
    all_fields = parent_fields + cls.fields
    has_parent = len(parent_fields) > 0

    # Inherit parent aliases into this class's alias map
    if parent_fields:
        if parent_name and parent_name in _aliases:
            if cls.name not in _aliases:
                _aliases[cls.name] = {}
            for k, v in _aliases[parent_name].items():
                _aliases[cls.name].setdefault(k, v)

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
        eq_fn.hides_parent = has_parent
        cls.add_method(eq_fn)

    # Generate __repr__
    if not cls.has_method("__repr__"):
        repr_fn = build_repr(cls, all_fields)
        repr_fn.hides_parent = has_parent
        cls.add_method(repr_fn)

    # Generate __hash__ for frozen models
    if frozen and not cls.has_method("__hash__"):
        hash_fn = build_hash(cls, all_fields)
        hash_fn.hides_parent = has_parent
        cls.add_method(hash_fn)

    # Generate ordering methods
    if order and all_fields:
        for dunder in ORDER_DUNDERS:
            if cls.has_method(dunder):
                raise MacroError(
                    f"@model(order=True) cannot overwrite '{dunder}' "
                    f"defined in class '{cls.name}'"
                )
        for fn in build_order(cls, all_fields):
            fn.hides_parent = has_parent
            cls.add_method(fn)

    cls.set_match_args([f.name for f in all_fields])
    _model_records.add(cls.name)

    for fn in [
        _build_json_decode(cls, all_fields),
        _build_from_json(cls),
        _build_try_from_json(cls),
        _build_json_encode(cls, all_fields),
        _build_to_json(cls),
        _build_save_json(cls),
        _build_load_json(cls),
        _build_try_load_json(cls),
    ]:
        fn.hides_parent = has_parent
        cls.add_method(fn)
