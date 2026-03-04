# Test that ReadOnlySpanLike[T] cannot coerce to mutable Span[T].
from tpy import Int32, Span, ReadOnlySpanLike

def accept_mut(s: Span[Int32]) -> None:
    pass

def f(c: ReadOnlySpanLike[Int32]) -> None:
    accept_mut(c)  # tpyc: error(/Type mismatch/)

def main() -> None:
    pass

main()
