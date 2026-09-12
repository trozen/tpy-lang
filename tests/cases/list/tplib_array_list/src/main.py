# Test tplib.ArrayList: fixed-capacity list imported from the standard library.
from tpy import int32, Span, Array, copy
from tplib import ArrayList


def main() -> None:
    # -- basics: empty, append, len, getitem --
    a = ArrayList[int32, 8]()
    print(len(a))               # 0
    a.append(10)
    a.append(20)
    a.append(30)
    print(len(a))               # 3
    print(a[0])                 # 10
    print(a[2])                 # 30

    # -- setitem --
    a[1] = 99
    print(a[1])                 # 99

    # -- pop --
    print(a.pop())              # 30
    print(len(a))               # 2

    # -- insert --
    a.insert(0, 5)
    print(a[0])                 # 5
    print(a[1])                 # 10
    print(len(a))               # 3

    # -- pop(index) --
    print(a.pop(1))             # 10
    print(len(a))               # 2

    # -- index, count --
    a.append(5)
    print(a.index(5))           # 0
    print(a.count(5))           # 2
    print(a.index(99))          # 1

    # -- remove --
    a.remove(99)
    print(len(a))               # 2
    print(a[0])                 # 5
    print(a[1])                 # 5

    # -- reverse --
    a[1] = 7
    a.reverse()
    print(a[0])                 # 7
    print(a[1])                 # 5

    # -- copy --
    b = copy(a)
    b[0] = 100
    print(a[0])                 # 7 (original unchanged)
    print(b[0])                 # 100

    # -- iter --
    c = ArrayList[int32, 4]()
    c.append(1)
    c.append(2)
    c.append(3)
    for x in c:
        print(x)
    # iterate again (fresh iterator each time)
    for x in c:
        print(x)

    # -- clear --
    a.clear()
    print(len(a))               # 0

    # -- construct from Span --
    arr: Array[int32, 3] = [10, 20, 30]
    s: Span[int32] = arr
    d = ArrayList[int32, 8](s)
    print(len(d))               # 3
    print(d[0])                 # 10
    print(d[2])                 # 30
    d.append(40)
    print(len(d))               # 4


main()
