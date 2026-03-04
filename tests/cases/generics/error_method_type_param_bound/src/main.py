# Error: per-method Default bound violated by concrete type
from tpy import Int32, Own, Default, make_default

class Opaque:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

class Holder[T]:
    val: T

    def __init__(self, val: Own[T]) -> None:
        self.val = val

    def make_new[T: Default](self) -> T:
        return make_default()

def main() -> None:
    h = Holder[Opaque](Opaque(1))
    h.make_new()  # tpyc: error(/Default/)

main()
