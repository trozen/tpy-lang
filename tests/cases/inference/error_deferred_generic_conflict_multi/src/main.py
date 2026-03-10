# Deferred generic inference: conflicting constraints for same type param
# across method calls when type is not yet fully resolved
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

def main() -> None:
    p = Pair()
    p.set_a(Int32(1))   # T = Int32
    p.set_a(Int64(2))   # tpyc: error(/Conflicting type inference for 'T'/)

main()
