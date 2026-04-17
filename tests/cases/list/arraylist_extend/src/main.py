# Test ArrayList.extend(): bulk append from span and iterable sources
from tpy import Int32, Array, Span, copy
from tplib import ArrayList


def main() -> None:
    # extend from another ArrayList (Spannable)
    a = ArrayList[Int32, 16]()
    a.append(1)
    a.append(2)
    b = ArrayList[Int32, 16]()
    b.append(10)
    b.append(20)
    b.append(30)
    a.extend(b)
    print(len(a))       # 5
    for x in a:
        print(x)

    # extend from Array/Span
    c = ArrayList[Int32, 8]()
    arr: Array[Int32, 3] = [100, 200, 300]
    s: Span[Int32] = arr
    c.extend(s)
    print(len(c))       # 3
    print(c[0])         # 100
    print(c[2])         # 300


main()
