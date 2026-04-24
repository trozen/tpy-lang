# String literals have static storage (rodata) and are trivially safe to
# borrow from. StrView locals initialized from a literal are therefore
# param-derived and can survive the FlowFacts intersect at loop exit.
# Regression: previously `sv: StrView = ""; for ...: sv = item; return sv`
# was rejected because the literal-init didn't seed param-provenance.
from tpy import StrView

def first_or_default(items: list[str], default: str) -> StrView:
    sv: StrView = ""
    for item in items:
        sv = item
        break
    else:
        sv = default
    return sv

def default_literal_only(items: list[str]) -> StrView:
    sv: StrView = ""
    for item in items:
        sv = item
    return sv

def conditional_literal_or_param(flag: bool, p: str) -> StrView:
    sv: StrView = "fallback"
    if flag:
        sv = p
    return sv

def ternary_literal_branch(flag: bool, p: str) -> StrView:
    sv: StrView = p if flag else "empty"
    return sv

def main() -> None:
    print(first_or_default(["hi"], "fallback"))
    print(first_or_default([], "fallback"))
    print(default_literal_only(["a", "b", "c"]))
    print(default_literal_only([]))
    print(conditional_literal_or_param(True, "param"))
    print(conditional_literal_or_param(False, "param"))
    print(ternary_literal_branch(True, "param"))
    print(ternary_literal_branch(False, "param"))

main()
