# Test tpy.deref(): dereference Ptr, ReadOnlyPtr, and Deref protocol params.
from tpy import Int32, Ptr, ReadOnlyPtr, Deref, deref

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

main()
