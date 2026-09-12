from tpy import int32, Span, readonly

def modify_span(s: Span[readonly[int32]]) -> None:
    s[0] = 42  # tpyc: error(/Cannot assign to elements of Span\[readonly/)

def main() -> int32:
    return 0
