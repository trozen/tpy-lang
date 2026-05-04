from tpy import Int32, Span

def bad() -> None:
    s: Span[Int32] = [1, 2, 3]  # tpyc: error(/Cannot take address of a temporary or expression/)
