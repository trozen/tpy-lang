# Test Span and Span[readonly[T]] constructors from pointer + length.
from tpy import Int32, Ptr, Span, Array, readonly, take_ptr

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])

    # Mutable span from Ptr
    p = take_ptr(arr[0])
    s = Span(p, 3)
    print(s[0])
    print(s[1])
    print(s[2])

    # Read-only span from Ptr[readonly[...]]
    rp: Ptr[readonly[Int32]] = take_ptr(arr[0])
    rs = Span(rp, 3)
    print(rs[0])
    print(rs[1])
    print(rs[2])

main()
