# Ptr[readonly[T]].span() returns Span[readonly[T]], not assignable to Span[T]
from tpy import Ptr, Span, Int32, readonly, take_ptr

def bad(p: Ptr[readonly[Int32]], n: Int32) -> None:
    s: Span[Int32] = p.span(n)  # tpyc: error(/readonly/)

def main() -> None:
    x = Int32(1)
    bad(take_ptr(x), Int32(1))

main()
