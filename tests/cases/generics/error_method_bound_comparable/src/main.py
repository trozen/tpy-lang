# Error: per-method Comparable bound violated by concrete type
from tpy import int32, Own, Comparable

class Opaque:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

class Holder[T]:
    val: T

    def __init__(self, val: Own[T]) -> None:
        self.val = val

    def smaller[T: Comparable](self, other: T) -> T:
        if self.val < other:
            return self.val
        return other

def main() -> None:
    h = Holder[Opaque](Opaque(1))
    h.smaller(Opaque(2))  # tpyc: error(/Comparable/)

main()
