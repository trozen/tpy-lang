# Test that Spannable[T] cannot coerce to mutable Span[T].
from tpy import Int32, Span, Spannable

def accept_mut(s: Span[Int32]) -> None:
    pass

def f(c: Spannable[Int32]) -> None:
    accept_mut(c)  # tpyc: error(/Type mismatch/)

def main() -> None:
    pass

main()
