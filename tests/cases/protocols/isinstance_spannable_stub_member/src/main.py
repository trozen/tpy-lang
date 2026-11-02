# isinstance() on Optional[Spannable[T]] narrows away None at compile time;
# a Span answers through the stub's `__span__`, which CPython's list lacks
# (BUGS.md#isinstance-protocol-signatures-vs-names row 7).
from tpy import int32, Spannable, Span, Array


def sum_span(items: Spannable[int32] | None = None) -> int32:
    if isinstance(items, Spannable):  # tpyc: ok
        total: int32 = 0
        for x in items:
            total += x
        return total
    return -1


def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]
    s: Span[int32] = arr

    # a Span over an Array is Spannable; the None default is not
    print("sum_span", sum_span(s), sum_span())


main()
