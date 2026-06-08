# tpy: macro_module
"""Function macro exercising ctx.lookup_function_signatures: reads a
callee's overload signatures (params + return type) -- module-local,
imported, aliased, zero-param, unknown (None), and overloaded (a
multi-element list, not None) -- and retypes a bool-string local to the
type of the slot it flows into, proving the looked-up types drive a real
deduction."""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast,
    VarDecl, StrLiteral,
)


def _names(sig):
    return [n for n, _ in sig.params]


@function_macro
def deduce_from_slot(ctx: FunctionMacroContext) -> None:
    local = ctx.lookup_function_signatures("local_sink")
    if local is None or len(local) != 1:
        ctx.error(f"expected one 'local_sink' signature: {local!r}")
    sig = local[0]
    if _names(sig) != ["flag", "label"]:
        ctx.error(f"unexpected local_sink param names: {sig!r}")
    flag_t = sig.params[0][1]
    if flag_t is None or not flag_t.is_bool:
        ctx.error("expected 'flag' param typed bool")
    if sig.params[1][1] is None or not sig.params[1][1].is_str:
        ctx.error("expected 'label' param typed str")
    if sig.return_type is None or not sig.return_type.is_bool:
        ctx.error("expected 'local_sink' to return bool")

    # `paint` is imported, not module-local, so this only resolves if
    # cross-module visibility works.
    imported = ctx.lookup_function_signatures("paint")
    if imported is None or len(imported) != 1:
        ctx.error(f"expected one imported 'paint' signature: {imported!r}")
    psig = imported[0]
    if psig.params[0][0] != "c" or psig.params[0][1] is None \
            or not psig.params[0][1].is_enum:
        ctx.error("expected imported paint(c: <enum>)")
    if psig.return_type is None or not psig.return_type.is_bool:
        ctx.error("expected imported 'paint' to return bool")

    if ctx.lookup_function_signatures("no_such_fn") is not None:
        ctx.error("unknown function name should resolve to None")

    # >1 overload: now visible as a multi-element list (the caller, not the
    # API, decides what to do with ambiguity).
    amb = ctx.lookup_function_signatures("amb")
    if amb is None or len(amb) != 2:
        ctx.error(f"overloaded 'amb' should give 2 signatures: {amb!r}")
    # pin each overload's param->return pairing, so the str overload's whole
    # signature is exercised -- an aggregate "some overload returns str" check
    # would pass even if both overloads were the bool one.
    amb_shapes = set()
    for s in amb:
        pt, rt = s.params[0][1], s.return_type
        if pt is None or rt is None:
            ctx.error(f"'amb' overload missing param/return type: {amb!r}")
        if pt.is_bool and rt.is_bool:
            amb_shapes.add("bool")
        elif pt.is_str and rt.is_str:
            amb_shapes.add("str")
    if amb_shapes != {"bool", "str"}:
        ctx.error(f"'amb' overloads should be (bool)->bool and (str)->str: {amb!r}")

    # zero-param function: one signature with an empty param tuple.
    main_sigs = ctx.lookup_function_signatures("main")
    if main_sigs is None or len(main_sigs) != 1 \
            or main_sigs[0].params != ():
        ctx.error(f"zero-param 'main' should give one empty sig: {main_sigs!r}")
    # `-> None` resolves to the void type (named "None"), not a missing
    # return_type -- the only callee covering the void-return shape.
    main_ret = main_sigs[0].return_type
    if main_ret is None or main_ret.name != "None":
        ctx.error(f"'main' (-> None) should return void: {main_sigs!r}")

    # an aliased import resolves under its local name, like the direct one.
    aliased = ctx.lookup_function_signatures("painter")
    if aliased is None or len(aliased) != 1 or _names(aliased[0]) != ["c"]:
        ctx.error(f"aliased 'painter' should resolve like 'paint': {aliased!r}")

    # the program only compiles if `flag_t` is the real bool slot type, so
    # this retype proves the lookup returned a usable type.
    for stmt in list(ctx.body):
        if (isinstance(stmt, VarDecl) and stmt.type is None
                and isinstance(stmt.init, StrLiteral)
                and stmt.init.value in ("true", "false")):
            ctx.replace_expr(
                stmt.init, ast.bool_lit(stmt.init.value == "true"))
            ctx.annotate_local(stmt.name, flag_t)
