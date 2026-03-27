# StrView return from free function call that returns owned str dangles.
from tpy import StrView

def transform(s: str) -> str:
    return s + "!"

def bad_with_args(s: str) -> StrView:
    return transform(s)  # tpyc: error(/Cannot return StrView referencing a local or temporary/)
