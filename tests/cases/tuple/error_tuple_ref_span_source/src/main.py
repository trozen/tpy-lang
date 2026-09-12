# Span[readonly[T]] elements are readonly -- cannot return as mutable reference in tuple.
from tpy import int32, Span, readonly

class Point:
    x: int32
    y: int32

def get_first(s: Span[readonly[Point]]) -> tuple[Point, int32]:
    return (s[0], int32(1))  # tpyc: error(/readonly.*reference/)
