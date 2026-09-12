# Test tpy.deref(): dereference Ptr, Ptr[readonly[...]], user types, and Deref protocol params.
from tpy import int32, Ptr, Deref, deref, readonly, take_ptr

class Box:
    _val: int32
    def __init__(self, v: int32) -> None:
        self._val = v
    def __deref__(self) -> int32:
        return self._val

def deref_protocol(p: Deref[int32]) -> int32:
    return deref(p)

def main() -> None:
    x: int32 = 42
    p: Ptr[int32] = take_ptr(x)
    print(deref(p))

    y: int32 = 77
    rp: Ptr[readonly[int32]] = take_ptr(y)
    print(deref(rp))

    z: int32 = 99
    print(deref_protocol(take_ptr(z)))

    w: int32 = 55
    print(deref_protocol(take_ptr(w)))

    # User-defined Deref type
    b = Box(33)
    print(deref(b))

main()
