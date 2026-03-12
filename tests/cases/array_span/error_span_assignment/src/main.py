from tpy import Int32, Span, readonly

def modify_span(s: Span[readonly[Int32]]) -> None:
    s[0] = 42  # tpyc: error(/Cannot assign to elements of Span\[readonly/)

def main() -> Int32:
    return 0
