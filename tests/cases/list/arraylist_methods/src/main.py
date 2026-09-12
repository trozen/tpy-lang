# Test ArrayList methods: __contains__, __eq__, __repr__, swap, truncate, index,
# count, remove, reverse, sort
from tpy import int32
from tplib import ArrayList

def test_contains() -> None:
    a = ArrayList[int32, 8]()
    a.append(10)
    a.append(20)
    a.append(30)
    print(20 in a)
    print(99 in a)

def test_eq() -> None:
    a = ArrayList[int32, 8]()
    a.append(1)
    a.append(2)
    b = ArrayList[int32, 8]()
    b.append(1)
    b.append(2)
    print(a == b)
    b.append(3)
    print(a == b)

def test_repr() -> None:
    a = ArrayList[int32, 4]()
    a.append(10)
    a.append(20)
    print(repr(a))

def test_swap() -> None:
    a = ArrayList[int32, 4]()
    a.append(1)
    a.append(2)
    a.append(3)
    a.swap(0, 2)
    print(a)

def test_truncate() -> None:
    a = ArrayList[int32, 8]()
    a.append(10)
    a.append(20)
    a.append(30)
    a.append(40)
    a.truncate(2)
    print(a)
    print(len(a))

def test_index() -> None:
    a = ArrayList[int32, 4]()
    a.append(10)
    a.append(20)
    a.append(30)
    print(a.index(20))

def test_count() -> None:
    a = ArrayList[int32, 8]()
    a.append(1)
    a.append(2)
    a.append(1)
    a.append(3)
    a.append(1)
    print(a.count(1))
    print(a.count(2))
    print(a.count(99))

def test_remove() -> None:
    a = ArrayList[int32, 8]()
    a.append(10)
    a.append(20)
    a.append(30)
    a.remove(20)
    print(a)

def test_reverse() -> None:
    a = ArrayList[int32, 8]()
    a.append(1)
    a.append(2)
    a.append(3)
    a.append(4)
    a.reverse()
    print(a)

def test_sort() -> None:
    # General case
    a = ArrayList[int32, 8]()
    a.append(5)
    a.append(3)
    a.append(1)
    a.append(4)
    a.append(2)
    a.sort()
    print(a)
    # Duplicates
    b = ArrayList[int32, 8]()
    b.append(3)
    b.append(1)
    b.append(3)
    b.append(2)
    b.append(1)
    b.sort()
    print(b)
    # Empty
    c = ArrayList[int32, 4]()
    c.sort()
    print(c)
    # Single element
    d = ArrayList[int32, 4]()
    d.append(42)
    d.sort()
    print(d)
    # Already sorted
    e = ArrayList[int32, 4]()
    e.append(1)
    e.append(2)
    e.append(3)
    e.sort()
    print(e)

def main() -> None:
    test_contains()
    test_eq()
    test_repr()
    test_swap()
    test_truncate()
    test_index()
    test_count()
    test_remove()
    test_reverse()
    test_sort()

main()
