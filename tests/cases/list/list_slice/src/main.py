# List slicing: basic, negative indices, clamping, empty, Array, type inference.
from tpy import Int32, Array, Span, ReadOnlySpan, readonly


def test_basic() -> None:
    items: list[int] = [10, 20, 30, 40, 50]
    print(items[1:3])
    print(items[:3])
    print(items[2:])
    print(items[:])


def test_negative() -> None:
    items: list[int] = [10, 20, 30, 40, 50]
    print(items[-2:])
    print(items[:-2])
    print(items[-4:-1])


def test_clamping() -> None:
    items: list[int] = [10, 20, 30]
    print(items[0:100])
    print(items[-100:2])
    print(items[-100:100])
    print(items[10:20])


def test_empty() -> None:
    items: list[int] = [10, 20, 30]
    print(items[2:1])
    print(items[5:5])
    print(len(items[1:1]))


def test_type_inference() -> None:
    items: list[Int32] = [Int32(1), Int32(2), Int32(3)]
    sub = items[0:2]  # tpyc: type(Span[Int32])
    print(sub)


def test_array() -> None:
    arr: Array[Int32, 5] = [Int32(1), Int32(2), Int32(3), Int32(4), Int32(5)]
    sub = arr[1:4]  # tpyc: type(Span[Int32])
    print(sub)


def test_span(s: Span[Int32]) -> None:
    sub = s[1:3]  # tpyc: type(Span[Int32])
    print(sub)


@readonly
def test_readonly_list(items: list[Int32]) -> None:
    sub = items[0:2]  # tpyc: type(ReadOnlySpan[Int32])
    print(sub)


def test_readonly_span_param(s: ReadOnlySpan[Int32]) -> None:
    sub = s[0:2]  # tpyc: type(ReadOnlySpan[Int32])
    print(sub)


def test_single_element() -> None:
    items: list[int] = [10, 20, 30]
    print(items[0:1])
    print(items[-1:])


test_basic()
print("---")
test_negative()
print("---")
test_clamping()
print("---")
test_empty()
print("---")
test_type_inference()
print("---")
test_array()
print("---")
span_src: list[Int32] = [Int32(10), Int32(20), Int32(30), Int32(40)]
test_span(span_src)
print("---")
test_readonly_list([Int32(10), Int32(20), Int32(30)])
print("---")
test_readonly_span_param([Int32(10), Int32(20), Int32(30), Int32(40)])
print("---")
test_single_element()
