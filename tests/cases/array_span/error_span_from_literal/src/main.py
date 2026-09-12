from tpy import int32, Span

def bad() -> None:
    s: Span[int32] = [1, 2, 3]  # tpyc: error(/Cannot take address of a temporary or expression/)
