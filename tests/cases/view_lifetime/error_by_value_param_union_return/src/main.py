# A by-value record returned into a union with a reference alternative: the
# union points at the parameter's copy, which dies at the return
# (docs/LANGUAGE_FEATURES.md: a parameter the function holds its own copy of).
# The scalar form, `def widen(x: int32) -> int32 | Sq`, is refused the same way.
from tpy import ValueType, int32


class Sq:
    def __init__(self, s: int32) -> None:
        self.s = s


class V(ValueType):
    def __init__(self, s: int32) -> None:
        self.s = s


def widen(x: V) -> V | Sq:
    return x  # tpyc: error(/Cannot return parameter 'x' \(the function's own copy\) as reference/)


def main() -> None:
    u = widen(V(42))
    if isinstance(u, V):
        print(u.s)


main()
