# Ptr[T] field accessed through @readonly receiver becomes ReadOnlyPtr[T],
# enabling read-only deref while preventing mutation through the pointer.
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
    def read_value(self) -> Int32:
        return self.ptr.__deref__().value

def read_through(c: readonly[Container]) -> Int32:
    return c.ptr.__deref__().value

def main() -> None:
    d = Data(Int32(42))
    c = Container()
    c.ptr = Ptr(d)
    print(c.read_value())
    print(read_through(c))

main()
