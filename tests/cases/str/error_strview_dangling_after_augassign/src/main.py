# Aug-assign on a literal-initialized str local allocates an owned
# std::string; the prior param-derived provenance from the literal
# initializer must be cleared so `return sv` where `sv: StrView = s` is
# caught as dangling.
from tpy import StrView

def bad_str_augmented() -> StrView:
    s: str = ""
    s += "x"
    sv: StrView = s
    return sv  # tpyc: error(/Cannot return StrView referencing a local or temporary/)
