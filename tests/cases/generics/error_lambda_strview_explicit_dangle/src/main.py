# Lambda body type already matches the StrView return slot, but `StrView(...)`
# wraps a temporary str -- still dangles. Distinct from the implicit-coercion
# form because body and return types match exactly; regression for the case
# where a type-mismatch heuristic would incorrectly skip the check.
from tpy import Own, copy, Fn, StrView

def apply(f: Fn[[StrView], StrView], init: StrView) -> Own[StrView]:
    return copy(f(init))

def main() -> None:
    apply(lambda s: StrView(s + "!"), "hello")  # tpyc: error(/dangling|StrView|local or temporary/)

main()
