# tpy: macro_module
# Call macro in a package, using macro_deps with a dotted module name.
from tpyc.macro_api import (
    CallMacroContext, MacroArg,
    call_macro, macro_deps, ast, Expr,
)

macro_deps("mypkg.helpers")


@call_macro
def make_tag(ctx: CallMacroContext, label_arg: MacroArg, value_arg: MacroArg) -> Expr:
    """Expand make_tag("x", val) -> mypkg.helpers.tag("x", val)."""
    helpers = ast.name("mypkg.helpers")
    return ast.method_call(helpers, "tag", [label_arg.expr, value_arg.expr])
