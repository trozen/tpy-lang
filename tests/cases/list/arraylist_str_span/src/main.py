# Test ArrayList.__str__ (via print/str) and __span__ (Span conversion)
from tpy import Int32, Span, readonly
from tplib import ArrayList


def sum_span(s: Span[readonly[Int32]]) -> Int32:
    total: Int32 = 0
    for x in s:
        total += x
    return total


def main() -> None:
    a = ArrayList[Int32, 8]()
    a.append(10)
    a.append(20)
    a.append(30)

    # __str__ via str()
    print(str(a))

    # __span__ returns mutable Span
    s: Span[Int32] = a.__span__()
    print(len(s))       # 3
    print(sum_span(s))  # 60


main()
