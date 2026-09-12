# Deferred generic inference: conflicting constraints for same type param
# across method calls when type is not yet fully resolved
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

def main() -> None:
    p = Pair()
    p.set_a(int32(1))   # T = int32
    p.set_a(int64(2))   # tpyc: error(/Conflicting type inference for 'T'/)

main()
