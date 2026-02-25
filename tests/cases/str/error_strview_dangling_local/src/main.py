# Returning a local StrView variable is caught as a dangling reference
from tpy import StrView, String

def bad_local() -> StrView:
    s: String = String("hello")
    sv: StrView = StrView(s)
    return sv  # tpyc: error(/Cannot return StrView referencing a local/)
