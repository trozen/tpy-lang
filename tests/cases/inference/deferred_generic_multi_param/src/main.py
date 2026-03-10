# Deferred generic inference: multiple type params resolved from method calls
from tpy import Int32, Int64

class Pair[T, U]:
    a: T
    b: U
    def __init__(self) -> None:
        pass
    def set_a(self, val: T) -> None:
        self.a = val
    def set_b(self, val: U) -> None:
        self.b = val
    def set_both(self, a: T, b: U) -> None:
        self.a = a
        self.b = b
    def get_a(self) -> T:
        return self.a
    def get_b(self) -> U:
        return self.b

def main() -> None:
    # Incremental resolution: set_a then set_b
    p1 = Pair()  # tpyc: type(/Pair\[Int32, Int64\]/)
    p1.set_a(Int32(10))   # T = Int32, U still unknown
    p1.set_b(Int64(20))   # U = Int64, all resolved -> Pair[Int32, Int64]
    print(p1.get_a())
    print(p1.get_b())

    # Single-call resolution: set_both resolves T and U at once
    p2 = Pair()  # tpyc: type(/Pair\[Int32, Int64\]/)
    p2.set_both(Int32(1), Int64(2))
    print(p2.get_a())
    print(p2.get_b())

main()
