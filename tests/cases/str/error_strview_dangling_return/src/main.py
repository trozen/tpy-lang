# Returning StrView referencing a local is caught as a dangling reference
from tpy import StrView, String

def bad_constructor() -> StrView:
    s: String = String("hello")
    return StrView(s)  # tpyc: error(/Cannot return StrView referencing a local/)
