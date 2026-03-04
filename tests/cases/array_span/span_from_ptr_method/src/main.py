# Test Ptr[T].span(n) -> Span[T] and ReadOnlyPtr[T].span(n) -> ReadOnlySpan[T].
from tpy import Int32, Ptr, ReadOnlyPtr, Span, ReadOnlySpan, Array

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])

    # Mutable span from Ptr.span()
    p = Ptr(arr[0])
    s: Span[Int32] = p.span(3)
    s[0] = 42
    print(arr[0])  # 42
    print(s[1])    # 20
    print(s[2])    # 30

    # Read-only span from ReadOnlyPtr.span()
    rp = ReadOnlyPtr(arr[0])
    rs: ReadOnlySpan[Int32] = rp.span(3)
    print(rs[0])   # 42
    print(rs[1])   # 20
    print(rs[2])   # 30

main()
