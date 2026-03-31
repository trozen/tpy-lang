# tpy: macro_module
from tpyc.macro_api import ClassInfo, class_macro, ast

@class_macro
def bad(cls: ClassInfo) -> None:
    ast.quote_expr("x = 1")
