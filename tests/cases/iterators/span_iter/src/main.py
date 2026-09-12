# Test iter() on Span/Span[readonly[T]]: manual iteration, Iterator[T] param, print.
from typing import Iterator
from tpy import int32, Array, Span, readonly

def consume(it: Iterator[int32]) -> None:
    for x in it:
        print(x)

def main() -> None:
    a: Array[int32, 3] = [10, 20, 30]

    # iter() on mutable Span
    s: Span[int32] = a
    it = iter(s)
    for x in it:
        print(x)

    # iter() on Span[readonly[T]]
    ro: Span[readonly[int32]] = a
    it2 = iter(ro)
    for x in it2:
        print(x)

    # Pass iter(span) to Iterator[T] param
    consume(iter(s))

    # print(iter(span)) -- should print <iterator>
    print(iter(s))

main()
