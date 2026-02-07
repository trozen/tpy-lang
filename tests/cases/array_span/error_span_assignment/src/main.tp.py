from tpy import Int32, Span

def modify_span(s: Span[Int32]) -> None:
    s[0] = 42  # tpyc: error(/Cannot assign to elements of Span/)

def main() -> Int32:
    return 0
