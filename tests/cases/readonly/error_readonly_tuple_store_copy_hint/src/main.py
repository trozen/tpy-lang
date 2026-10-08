# A field (or subscript) store refuses a readonly tuple at its reference
# element, as `self.g = b` refuses a readonly[Box] scalar, and names copy(t).
from tpy import int32, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class O:
    f: tuple[Box, int32]

    def __init__(self) -> None:
        self.f = (Box(0), 0)


def store(o: O, t: readonly[tuple[Box, int32]]) -> None:
    o.f = t  # tpyc: error(/Cannot pass readonly\[Box\] as mutable Box in assignment \(tuple element 0\) -- use copy\(t\) to store a mutable copy/)


def main() -> None:
    store(O(), (Box(1), 2))


main()
