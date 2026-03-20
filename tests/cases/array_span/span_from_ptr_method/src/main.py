# Test Ptr[T].span(n) -> Span[T] and Ptr[readonly[T]].span(n) -> Span[readonly[T]].
from tpy import Int32, Ptr, Span, Array, readonly, take_ptr

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])

    # Mutable span from Ptr.span()
    p = take_ptr(arr[0])
    s: Span[Int32] = p.span(3)
    s[0] = 42
    print(arr[0])  # 42
    print(s[1])    # 20
    print(s[2])    # 30

    # Read-only span from Ptr[readonly[...]].span()
    rp: Ptr[readonly[Int32]] = take_ptr(arr[0])
    rs: Span[readonly[Int32]] = rp.span(3)
    print(rs[0])   # 42
    print(rs[1])   # 20
    print(rs[2])   # 30

main()
