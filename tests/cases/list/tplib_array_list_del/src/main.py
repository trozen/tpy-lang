# Test del on ArrayList: __delitem__ removes element and shifts remaining
from tpy import Int32
from tplib import ArrayList


def main() -> None:
    a = ArrayList[Int32, 8]()
    a.append(10)
    a.append(20)
    a.append(30)
    a.append(40)
    print(len(a))       # 4

    # Delete middle element
    del a[1]
    print(len(a))       # 3
    print(a[0])         # 10
    print(a[1])         # 30
    print(a[2])         # 40

    # Delete first element
    del a[0]
    print(len(a))       # 2
    print(a[0])         # 30
    print(a[1])         # 40

    # Delete last element
    del a[1]
    print(len(a))       # 1
    print(a[0])         # 30

    # Delete only remaining element
    del a[0]
    print(len(a))       # 0


main()
