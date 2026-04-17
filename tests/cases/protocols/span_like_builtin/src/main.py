# Test that builtin types and user types (ArrayList) conform to Spannable[T].
from tpy import Int32, Array, Span, Spannable, readonly
from tplib import ArrayList

def sum_span(c: Spannable[Int32]) -> Int32:
    total: Int32 = 0
    for x in c:
        total += x
    return total

def test_list() -> None:
    data: list[Int32] = [10, 20, 30]
    print(sum_span(data))

def test_array() -> None:
    data: Array[Int32, 3] = [1, 2, 3]
    print(sum_span(data))

def test_span() -> None:
    data: list[Int32] = [7, 8, 9]
    s: Span[Int32] = data
    print(sum_span(s))

def test_ro_span() -> None:
    data: list[Int32] = [4, 5, 6]
    s: Span[readonly[Int32]] = data
    print(sum_span(s))

def test_arraylist() -> None:
    al = ArrayList[Int32, 4]()
    al.append(Int32(100))
    al.append(Int32(200))
    print(sum_span(al))

test_list()
test_array()
test_span()
test_ro_span()
test_arraylist()
print("done")
