# Test that builtin types (list, Array, Span, ReadOnlySpan, StaticList) conform
# to ReadOnlySpanLike[T].
from tpy import Int32, Array, Span, ReadOnlySpan, StaticList, ReadOnlySpanLike

def sum_span(c: ReadOnlySpanLike[Int32]) -> Int32:
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
    s: ReadOnlySpan[Int32] = data
    print(sum_span(s))

def test_static_list() -> None:
    sl: StaticList[Int32, 4] = StaticList()
    sl.append(Int32(100))
    sl.append(Int32(200))
    print(sum_span(sl))

test_list()
test_array()
test_span()
test_ro_span()
test_static_list()
print("done")
