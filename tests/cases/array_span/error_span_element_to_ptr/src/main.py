from tpy import int32, Ptr, Span, readonly

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def modify(p: Ptr[Point]) -> None:
    p.x = 999

def bad(span: Span[readonly[Point]]) -> None:
    modify(span[0])  # tpyc: error(/Cannot take mutable pointer to read-only/)
