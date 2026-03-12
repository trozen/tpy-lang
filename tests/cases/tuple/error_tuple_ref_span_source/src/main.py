# Span[readonly[T]] elements are readonly -- cannot return as mutable reference in tuple.
from tpy import Int32, Span, readonly

class Point:
    x: Int32
    y: Int32

def get_first(s: Span[readonly[Point]]) -> tuple[Point, Int32]:
    return (s[0], Int32(1))  # tpyc: error(/readonly.*reference/)
