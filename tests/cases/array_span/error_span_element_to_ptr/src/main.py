from tpy import Int32, Ptr, ReadOnlySpan

class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x

def modify(p: Ptr[Point]) -> None:
    p.x = 999

def bad(span: ReadOnlySpan[Point]) -> None:
    modify(span[0])  # tpyc: error(/Cannot take mutable pointer to read-only/)
