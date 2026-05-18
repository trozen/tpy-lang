# Ptr[readonly[T]] is a read-only pointer.
# Verifies the pointer is read-only: deref works, writes are rejected.
from tpy import Int32, Ptr, readonly

class Data:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

def read_via_ptr(p: Ptr[readonly[Data]]) -> Int32:
    return p.__deref__().value

def main() -> None:
    d = Data(Int32(42))
    p: Ptr[readonly[Data]] = d
    print(read_via_ptr(p))

main()
