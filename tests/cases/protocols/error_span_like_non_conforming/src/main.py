# Test that non-conforming type is rejected for Spannable[T] parameter.
from tpy import int32, Spannable

class NoSpan:
    def __init__(self) -> None:
        pass

def f(c: Spannable[int32]) -> int32:
    total: int32 = 0
    for x in c:
        total += x
    return total

def main() -> None:
    f(NoSpan())  # tpyc: error(/does not conform to protocol Spannable/)

main()
