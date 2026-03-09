# Test that types with __iter__() -> SpanIter[T] auto-conform to NativeIterable[T]
from tpy import Int32, NativeIterable
from tplib import ArrayList

def sum_items(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    # ArrayList has __iter__() -> SpanIter -> auto NativeIterable
    a = ArrayList[Int32, 8]()
    a.append(1)
    a.append(2)
    a.append(3)
    print(sum_items(a))

    # For loop over ArrayList
    for x in a:
        print(x)

main()
