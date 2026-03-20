# Ptr[readonly[T]].span() returns Span[readonly[T]], not Span[T]
from tpy import Ptr, Span, Int32, readonly, take_ptr

def read_span(s: Span[readonly[Int32]]) -> None:
    for v in s:
        print(v)

def from_readonly_ptr(p: Ptr[readonly[Int32]], n: Int32) -> None:
    s = p.span(n)  # tpyc: type(Span[readonly[Int32]])
    read_span(s)

def from_mutable_ptr(p: Ptr[Int32], n: Int32) -> None:
    s = p.span(n)  # tpyc: type(Span[Int32])
    read_span(s)

def main() -> None:
    a = Int32(10)
    b = Int32(20)

    mp: Ptr[Int32] = take_ptr(a)
    cp: Ptr[readonly[Int32]] = take_ptr(b)

    print("mutable ptr span:")
    from_mutable_ptr(mp, Int32(1))

    print("const ptr span:")
    from_readonly_ptr(cp, Int32(1))

main()
