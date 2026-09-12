# Test ArrayList.extend() from a plain Iterable (non-span path)
from tpy import int32
from tplib import ArrayList


def main() -> None:
    a = ArrayList[int32, 16]()
    a.append(1)

    # extend from list (Iterable, not Spannable)
    items: list[int32] = [10, 20, 30]
    a.extend(items)
    print(len(a))       # 4
    print(a[0])         # 1
    print(a[1])         # 10
    print(a[3])         # 30


main()
