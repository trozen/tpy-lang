# Test Span and ReadOnlySpan constructors from pointer + length.
from tpy import Int32, Ptr, ReadOnlyPtr, Span, ReadOnlySpan, Array

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])

    # Mutable span from Ptr
    p = Ptr(arr[0])
    s = Span(p, 3)
    print(s[0])
    print(s[1])
    print(s[2])

    # Read-only span from ReadOnlyPtr
    rp = ReadOnlyPtr(arr[0])
    rs = ReadOnlySpan(rp, 3)
    print(rs[0])
    print(rs[1])
    print(rs[2])

main()
