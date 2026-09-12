# Test that builtin types and user types (ArrayList) conform to Spannable[T].
from tpy import int32, Array, Span, Spannable, readonly
from tplib import ArrayList

def sum_span(c: Spannable[int32]) -> int32:
    total: int32 = 0
    for x in c:
        total += x
    return total

def test_list() -> None:
    data: list[int32] = [10, 20, 30]
    print(sum_span(data))

def test_array() -> None:
    data: Array[int32, 3] = [1, 2, 3]
    print(sum_span(data))

def test_span() -> None:
    data: list[int32] = [7, 8, 9]
    s: Span[int32] = data
    print(sum_span(s))

def test_ro_span() -> None:
    data: list[int32] = [4, 5, 6]
    s: Span[readonly[int32]] = data
    print(sum_span(s))

def test_arraylist() -> None:
    al = ArrayList[int32, 4]()
    al.append(int32(100))
    al.append(int32(200))
    print(sum_span(al))

test_list()
test_array()
test_span()
test_ro_span()
test_arraylist()
print("done")
