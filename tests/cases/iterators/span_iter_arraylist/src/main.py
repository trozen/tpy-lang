# Test ArrayList.__iter__ returns SpanIter and works with Iterable[T].
from tpy import Int32, SpanIter
from tplib.array_list import ArrayList
from typing import Iterable

def consume(it: Iterable[Int32]) -> None:
    for x in it:
        print(x)

def main() -> None:
    a = ArrayList[Int32, 8]()
    a.append(Int32(1))
    a.append(Int32(2))
    a.append(Int32(3))

    # Direct __iter__() returns SpanIter
    it: SpanIter[Int32] = a.__iter__()
    consume(it)

main()
