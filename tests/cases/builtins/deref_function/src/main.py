# Test tpy.deref(): dereference Ptr, ReadOnlyPtr, user types, and Deref protocol params.
from tpy import Int32, Ptr, ReadOnlyPtr, Deref, deref

class Box:
    _val: Int32
    def __init__(self, v: Int32) -> None:
        self._val = v
    def __deref__(self) -> Int32:
        return self._val

def deref_protocol(p: Deref[Int32]) -> Int32:
    return deref(p)

def main() -> None:
    x: Int32 = 42
    p: Ptr[Int32] = Ptr(x)
    print(deref(p))

    y: Int32 = 77
    rp: ReadOnlyPtr[Int32] = ReadOnlyPtr(y)
    print(deref(rp))

    z: Int32 = 99
    print(deref_protocol(Ptr(z)))

    w: Int32 = 55
    print(deref_protocol(ReadOnlyPtr(w)))

    # User-defined Deref type
    b = Box(33)
    print(deref(b))

main()
