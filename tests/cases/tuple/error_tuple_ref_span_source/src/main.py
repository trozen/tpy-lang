# ReadOnlySpan elements are readonly -- cannot return as mutable reference in tuple.
from tpy import Int32, ReadOnlySpan

class Point:
    x: Int32
    y: Int32

def get_first(s: ReadOnlySpan[Point]) -> tuple[Point, Int32]:
    return (s[0], Int32(1))  # tpyc: error(/readonly.*reference/)
