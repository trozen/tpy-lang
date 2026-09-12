# Deferred generic inference: multiple type params resolved from method calls
from tpy import int32, int64

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
    p1 = Pair()  # tpyc: type(/Pair\[int32, int64\]/)
    p1.set_a(int32(10))   # T = int32, U still unknown
    p1.set_b(int64(20))   # U = int64, all resolved -> Pair[int32, int64]
    print(p1.get_a())
    print(p1.get_b())

    # Single-call resolution: set_both resolves T and U at once
    p2 = Pair()  # tpyc: type(/Pair\[int32, int64\]/)
    p2.set_both(int32(1), int64(2))
    print(p2.get_a())
    print(p2.get_b())

main()
