# tpy: macro_module
from tpyc.macro_api import ClassInfo, class_macro, ast

@class_macro
def bad(cls: ClassInfo) -> None:
    cls.add_method_from_source("def foo(self) -> None:\n    if")
