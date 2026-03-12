# Test SpanIter[T]: construct from Span/Span[readonly[T]], iterate, pass as Iterable.
from tpy import Int32, Array, Span, SpanIter, readonly
from typing import Iterable

def sum_iterable(it: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

def sum_readonly(rs: Span[readonly[Int32]]) -> Int32:
    it = SpanIter(rs)
    return sum_iterable(it)

def main() -> None:
    arr: Array[Int32, 5] = [10, 20, 30, 40, 50]
    s: Span[Int32] = arr

    # Iterate SpanIter from mutable Span
    it = SpanIter(s)
    for x in it:
        print(x)

    # SpanIter from Span[readonly[T]] (via function param)
    print(sum_readonly(s))

    # Pass SpanIter as Iterable[T]
    it2 = SpanIter(s)
    print(sum_iterable(it2))

main()
