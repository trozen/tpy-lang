# Ptr[readonly[T]] is a read-only pointer, so writes through it are rejected.
from tpy import int32, Ptr, readonly, take_ptr

class Data:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def main() -> None:
    d = Data(int32(42))
    p: Ptr[readonly[Data]] = take_ptr(d)
    p.value = int32(99)  # tpyc: error(/Cannot assign through read-only pointer/)

main()
