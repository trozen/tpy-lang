"""Tests that ReadOnlySpanLike[T] types can coerce to Span[T]."""
from tpy import Int32, Span, Array, StaticList

def sum_span(values: Span[Int32]) -> Int32:
    """Function accepting Span - any ReadOnlySpanLike[Int32] should work."""
    total: Int32 = 0
    for v in values:
        total += v
    return total

def sum_span_bigint(values: Span[int]) -> int:
    """Function accepting Span[int] (BigInt) - tests IntLiteral coercion."""
    total: int = 0
    for v in values:
        total += v
    return total

def main() -> None:
    # Array coerces to Span
    arr: Array[Int32, 4] = [1, 2, 3, 4]
    print(sum_span(arr))  # 10

    # StaticList coerces to Span
    sl: StaticList[Int32, 8] = StaticList[Int32, 8]()
    sl.append(10)
    sl.append(20)
    sl.append(30)
    print(sum_span(sl))  # 60

    # list coerces to Span
    lst: list[Int32] = [100, 200, 300, 400]
    print(sum_span(lst))  # 1000

    # Literal array coerces to Span (via PendingListType)
    print(sum_span([5, 5, 5, 5, 5]))  # 25

    # BigInt tests - IntLiteral elements coerce to BigInt
    # list[int] coerces to Span[int]
    bigint_list: list[int] = [1000, 2000, 3000]
    print(sum_span_bigint(bigint_list))  # 6000

    # PendingListType with IntLiteral → Span[int] (BigInt)
    print(sum_span_bigint([100, 200, 300]))  # 600

main()
