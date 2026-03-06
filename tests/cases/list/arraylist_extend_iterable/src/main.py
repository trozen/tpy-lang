# Test ArrayList.extend() from a plain Iterable (non-span path)
from tpy import Int32
from tplib import ArrayList


def main() -> None:
    a = ArrayList[Int32, 16]()
    a.append(1)

    # extend from list (Iterable, not ReadOnlySpanLike)
    items: list[Int32] = [10, 20, 30]
    a.extend(items)
    print(len(a))       # 4
    print(a[0])         # 1
    print(a[1])         # 10
    print(a[3])         # 30


main()
