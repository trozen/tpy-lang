# Builtin types (list, str, Array, etc.) conform to Iterable[T]:
# 1. Passed to Iterable[T] params  2. Explicit __iter__()  3. iter() builtin
from typing import Iterable
from tpy import Int32, Char, Array, Span, ReadOnlySpan, String, StrView, Ptr

def sum_items(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def count_chars(items: Iterable[Char]) -> Int32:
    n: Int32 = 0
    for c in items:
        n += 1
    return n

def sum_strs(items: Iterable[str]) -> str:
    result: str = ""
    for s in items:
        result = result + s + " "
    return result

def test_iterable_params() -> None:
    # list
    nums: list[Int32] = [10, 20, 30]
    print(sum_items(nums))

    # range
    print(sum_items(range(5)))

    # str
    print(count_chars("hello"))

    # Array
    arr: Array[Int32, 3] = [1, 2, 3]
    print(sum_items(arr))

    # Span (mutable, via Ptr)
    sp: Span[Int32] = Span(Ptr(arr[0]), 3)
    print(sum_items(sp))

    # ReadOnlySpan (via Array.__span__())
    rosp: ReadOnlySpan[Int32] = arr.__span__()
    print(sum_items(rosp))

    # list (another)
    sl: list[Int32] = [100, 200]
    print(sum_items(sl))

    # String
    s: String = String("ab")
    print(count_chars(s))

    # StrView
    sv: StrView = StrView("xyz")
    print(count_chars(sv))

    # dict (iterates over keys)
    d: dict[str, Int32] = {"a": 1, "b": 2}
    print(sum_strs(d))

def test_manual_iter() -> None:
    # Call __iter__() explicitly on concrete types
    nums: list[Int32] = [1, 2, 3]
    it = nums.__iter__()
    total: Int32 = 0
    for x in it:
        total += x
    print(total)

    # str.__iter__()
    chars: str = "hi"
    char_it = chars.__iter__()
    for c in char_it:
        print(c)

    # Array.__iter__()
    arr: Array[Int32, 2] = [7, 8]
    arr_it = arr.__iter__()
    for v in arr_it:
        print(v)

    # dict.__iter__()
    d: dict[str, Int32] = {"x": 10, "y": 20}
    d_it = d.__iter__()
    for k in d_it:
        print(k)

def test_iter_on_protocol(items: Iterable[Int32]) -> None:
    # __iter__() on a protocol-typed variable
    it = items.__iter__()
    total: Int32 = 0
    for x in it:
        total += x
    print(total)

def test_iter_builtin() -> None:
    # iter() builtin with builtin container types
    nums: list[Int32] = [4, 5, 6]
    it = iter(nums)
    total: Int32 = 0
    for x in it:
        total += x
    print(total)

    # iter() on dict
    d: dict[str, Int32] = {"p": 1, "q": 2}
    d_it = iter(d)
    for k in d_it:
        print(k)

def sum_bigints(items: Iterable[int]) -> int:
    # Iterable[int] (BigInt) -- expensive value type, const ref when unmutated
    total: int = 0
    for x in items:
        total = total + x
    return total

def main() -> None:
    test_iterable_params()
    test_manual_iter()
    proto_input: list[Int32] = [10, 20]
    test_iter_on_protocol(proto_input)
    test_iter_builtin()

    # Test Iterable[int] with list[int] (range path) and direct BigInt
    bigints: list[int] = [100, 200, 300]
    print(sum_bigints(bigints))

main()
