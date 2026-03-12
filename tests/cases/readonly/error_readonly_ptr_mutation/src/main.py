# Attempting to write through a Ptr[T] field in a @readonly context should fail
# because the Ptr becomes Ptr[readonly[T]] (read-only pointer) via readonly propagation.
from tpy import Int32, Ptr, readonly

class Data:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

class Container:
    ptr: Ptr[Data]

    def __init__(self) -> None:
        self.ptr = Ptr[Data]()

    @readonly
    def try_mutate(self) -> None:
        self.ptr.value = Int32(99)  # tpyc: error(/Cannot assign through read-only pointer/)

def main() -> None:
    pass

main()
