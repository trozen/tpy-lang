# A Ptr[T] field read through a readonly receiver (a @readonly method, a
# readonly[...] param) derefs to read its pointee: readonly is shallow through
# a Ptr, so the pointee keeps the access its type argument gives it.
from tpy import int32, Ptr, readonly, take_ptr

class Data:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

class Container:
    ptr: Ptr[Data]

    def __init__(self) -> None:
        self.ptr = Ptr[Data]()

    @readonly
    def read_value(self) -> int32:
        return self.ptr.__deref__().value

def read_through(c: readonly[Container]) -> int32:
    return c.ptr.__deref__().value

def main() -> None:
    d = Data(int32(42))
    c = Container()
    c.ptr = take_ptr(d)
    print(c.read_value())
    print(read_through(c))

main()
