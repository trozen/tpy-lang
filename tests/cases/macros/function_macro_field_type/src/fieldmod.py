# tpy: macro_module
"""Function macro: type a string-bool local from a record param's field.

Resolves the type of a field on the first record-typed param via
ctx.get_field_type and uses it to retype `x = "true"/"false"` locals.
Also probes get_method_return_type on the same param type.
"""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast,
    VarDecl, StrLiteral,
)


@function_macro
def field_typed_locals(ctx: FunctionMacroContext) -> None:
    field_t = None
    for _name, ptype in ctx.params:
        if ptype is None or not ptype.is_record:
            continue
        field_t = ctx.get_field_type(ptype, "flag")
        # 'tag' lives on the base class, so this exercises inherited-field lookup
        base_t = ctx.get_field_type(ptype, "tag")
        if base_t is None or not base_t.is_int32:
            ctx.error("expected inherited int32 field 'tag'")
        ret_t = ctx.get_method_return_type(ptype, "describe")
        if ret_t is None or not ret_t.is_str:
            ctx.error("expected str-returning method 'describe'")
        if ctx.get_field_type(ptype, "no_such_field") is not None:
            ctx.error("unknown field should resolve to None")
        if ctx.get_method_return_type(ptype, "no_such_method") is not None:
            ctx.error("unknown method should resolve to None")
        break
    if field_t is None or not field_t.is_bool:
        ctx.error("expected a record param with a bool field 'flag'")
    for stmt in list(ctx.body):
        if (isinstance(stmt, VarDecl) and stmt.type is None
                and isinstance(stmt.init, StrLiteral)
                and stmt.init.value in ("true", "false")):
            ctx.replace_expr(
                stmt.init, ast.bool_lit(stmt.init.value == "true"))
            ctx.annotate_local(stmt.name, field_t)
