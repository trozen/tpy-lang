# Ptr[readonly[T]].span() returns Span[readonly[T]], not Span[T]
from tpy import Ptr, Span, int32, readonly, take_ptr

def read_span(s: Span[readonly[int32]]) -> None:
    for v in s:
        print(v)

def from_readonly_ptr(p: Ptr[readonly[int32]], n: int32) -> None:
    s = p.span(n)  # tpyc: type(Span[readonly[int32]])
    read_span(s)

def from_mutable_ptr(p: Ptr[int32], n: int32) -> None:
    s = p.span(n)  # tpyc: type(Span[int32])
    read_span(s)

def main() -> None:
    a = int32(10)
    b = int32(20)

    mp: Ptr[int32] = take_ptr(a)
    cp: Ptr[readonly[int32]] = take_ptr(b)

    print("mutable ptr span:")
    from_mutable_ptr(mp, int32(1))

    print("const ptr span:")
    from_readonly_ptr(cp, int32(1))

main()
