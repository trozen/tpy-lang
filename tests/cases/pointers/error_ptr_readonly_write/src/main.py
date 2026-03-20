# Ptr[readonly[T]] is a read-only pointer, so writes through it are rejected.
from tpy import Int32, Ptr, readonly, take_ptr

class Data:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

def main() -> None:
    d = Data(Int32(42))
    p: Ptr[readonly[Data]] = take_ptr(d)
    p.value = Int32(99)  # tpyc: error(/Cannot assign through read-only pointer/)

main()
