# Test that Span[readonly[T]] rejects element assignment.
from tpy import int32, Span, readonly

def modify(s: Span[readonly[int32]]) -> None:
    s[0] = 42  # tpyc: error(/Cannot assign to elements of Span\[readonly/)

def main() -> int32:
    return 0
