# tpy: macro_module
"""Function macro that resolves module-visible names by string and retypes string-bool locals."""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast,
    VarDecl, StrLiteral,
)


@function_macro
def lookup_typed_locals(ctx: FunctionMacroContext) -> None:
    local_rec = ctx.lookup_imported_name("Gate")
    if local_rec is None or not local_rec.is_record:
        ctx.error("expected module-local record 'Gate' to resolve")
    imported_rec = ctx.lookup_imported_name("Lamp")
    if imported_rec is None or not imported_rec.is_record:
        ctx.error("expected imported record 'Lamp' to resolve")
    lit_t = ctx.get_field_type(imported_rec, "lit")
    if lit_t is None or not lit_t.is_bool:
        ctx.error("expected bool field 'lit' on imported Lamp")
    color = ctx.lookup_imported_name("Color")
    if color is None or not color.is_enum:
        ctx.error("expected enum 'Color' to resolve")
    if color.enum_members != ("RED", "GREEN"):
        ctx.error(f"unexpected Color members: {color.enum_members!r}")
    if ctx.lookup_imported_name("NoSuchThing") is not None:
        ctx.error("unknown name should resolve to None")
    # Switch is defined in lampmod but not imported here; per-module
    # visibility must keep it out of reach (not a flat-global lookup).
    if ctx.lookup_imported_name("Switch") is not None:
        ctx.error("type not imported into this module should resolve to None")

    field_t = ctx.get_field_type(local_rec, "flag")
    if field_t is None or not field_t.is_bool:
        ctx.error("expected bool field 'flag' on Gate")
    for stmt in list(ctx.body):
        if (isinstance(stmt, VarDecl) and stmt.type is None
                and isinstance(stmt.init, StrLiteral)
                and stmt.init.value in ("true", "false")):
            ctx.replace_expr(
                stmt.init, ast.bool_lit(stmt.init.value == "true"))
            ctx.annotate_local(stmt.name, field_t)
