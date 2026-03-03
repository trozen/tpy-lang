# Test that ReadOnlySpan[T] rejects element assignment.
from tpy import Int32, ReadOnlySpan

def modify(s: ReadOnlySpan[Int32]) -> None:
    s[0] = 42  # tpyc: error(/Cannot assign to elements of ReadOnlySpan/)

def main() -> Int32:
    return 0
