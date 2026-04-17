# Test that non-conforming type is rejected for Spannable[T] parameter.
from tpy import Int32, Spannable

class NoSpan:
    def __init__(self) -> None:
        pass

def f(c: Spannable[Int32]) -> Int32:
    total: Int32 = 0
    for x in c:
        total += x
    return total

def main() -> None:
    f(NoSpan())  # tpyc: error(/does not conform to protocol Spannable/)

main()
