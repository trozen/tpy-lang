# tpy: macro_module
"""Method-target function macro: introspects the enclosing record via
ctx.self_type (None for staticmethods / free functions) and annotates a
body local from one of the record's field types -- the pattern a
plugin-hosted resolver macro uses to type locals in method bodies.
"""
from tpyc.macro_api import function_macro, FunctionMacroContext


@function_macro
def probe(ctx: FunctionMacroContext) -> None:
    st = ctx.self_type
    if ctx.is_method and st is not None:
        ft = ctx.get_field_type(st, "n")
        ctx.annotate_local("doubled", ft)
        ctx.warning(
            f"method macro on {st.name}.{ctx.function_name}: "
            f"field n is {ft.name}")
    else:
        ctx.warning(
            f"macro on {ctx.function_name}: no self "
            f"(is_method={ctx.is_method})")
