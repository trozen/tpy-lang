# Test that Spannable[T] cannot coerce to mutable Span[T].
from tpy import int32, Span, Spannable

def accept_mut(s: Span[int32]) -> None:
    pass

def f(c: Spannable[int32]) -> None:
    accept_mut(c)  # tpyc: error(/Type mismatch/)

def main() -> None:
    pass

main()
