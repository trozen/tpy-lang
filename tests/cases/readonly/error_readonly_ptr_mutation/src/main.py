# Readonly does not reach through a Ptr field; a Ptr[readonly[T]] field
# protects its pointee, through a mutable receiver as well.
from tpy import int32, Ptr, readonly

class Data:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

class Container:
    ptr: Ptr[readonly[Data]]

    def __init__(self) -> None:
        self.ptr = Ptr[readonly[Data]]()

    def try_mutate(self) -> None:
        self.ptr.value = int32(99)  # tpyc: error(/Cannot assign through read-only pointer/)

def main() -> None:
    pass

main()
