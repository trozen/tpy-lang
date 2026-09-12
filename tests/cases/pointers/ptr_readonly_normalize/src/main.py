# Ptr[readonly[T]] is a read-only pointer.
# Verifies the pointer is read-only: deref works, writes are rejected.
from tpy import int32, Ptr, readonly

class Data:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def read_via_ptr(p: Ptr[readonly[Data]]) -> int32:
    return p.__deref__().value

def main() -> None:
    d = Data(int32(42))
    p: Ptr[readonly[Data]] = d
    print(read_via_ptr(p))

main()
