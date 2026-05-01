# Implicit-coercion form: lambda body has type str, return slot is StrView.
# Coercion materializes a temporary str then views it -- dangle.
from tpy import Own, copy, Fn, StrView

def apply(f: Fn[[StrView], StrView], init: StrView) -> Own[StrView]:
    return copy(f(init))

def main() -> None:
    apply(lambda s: s + "!", "hello")  # tpyc: error(/dangling|StrView|local or temporary/)

main()
