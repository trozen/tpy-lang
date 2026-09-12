# Test ArrayList.__iter__ returns SpanIter and works with Iterable[T].
from tpy import int32, SpanIter
from tplib.array_list import ArrayList
from typing import Iterable

def consume(it: Iterable[int32]) -> None:
    for x in it:
        print(x)

def main() -> None:
    a = ArrayList[int32, 8]()
    a.append(int32(1))
    a.append(int32(2))
    a.append(int32(3))

    # Direct __iter__() returns SpanIter
    it: SpanIter[int32] = a.__iter__()
    consume(it)

main()
