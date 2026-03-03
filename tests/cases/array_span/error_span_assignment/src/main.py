from tpy import Int32, ReadOnlySpan

def modify_span(s: ReadOnlySpan[Int32]) -> None:
    s[0] = 42  # tpyc: error(/Cannot assign to elements of ReadOnlySpan/)

def main() -> Int32:
    return 0
