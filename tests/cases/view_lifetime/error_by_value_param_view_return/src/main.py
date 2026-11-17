# A by-value ValueType record parameter is the function's own copy: a view
# of its field dies at the return, so it is refused as a local's is.
# (docs/LANGUAGE_FEATURES.md: a parameter the function holds its own copy of).
from tpy import ValueType, StrView


class V(ValueType):
    def __init__(self, name: str) -> None:
        self.name = name


def direct(x: V) -> StrView:
    return x.name  # tpyc: error(/Cannot return StrView referencing parameter 'x' \(the function's own copy\); use str or String to return an owned copy/)


def main() -> None:
    print(direct(V("n" * 30))[:2])


main()
