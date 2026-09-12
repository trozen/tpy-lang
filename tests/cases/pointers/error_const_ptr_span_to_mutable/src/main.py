# Ptr[readonly[T]].span() returns Span[readonly[T]], not assignable to Span[T]
from tpy import Ptr, Span, int32, readonly, take_ptr

def bad(p: Ptr[readonly[int32]], n: int32) -> None:
    s: Span[int32] = p.span(n)  # tpyc: error(/readonly/)

def main() -> None:
    x = int32(1)
    bad(take_ptr(x), int32(1))

main()
