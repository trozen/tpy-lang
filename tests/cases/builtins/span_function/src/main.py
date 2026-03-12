# Test tpy.span(): get ReadOnlySpan from ReadOnlySpanLike types.
from tpy import Int32, Span, ReadOnlySpan, ReadOnlySpanLike, Array, span, readonly_propagate

class Buffer:
    _data: list[Int32]
    def __init__(self) -> None:
        self._data = [10, 20, 30]
    @readonly_propagate
    def __span__(self) -> Span[readonly_propagate[Int32]]:
        return self._data

def span_len(x: ReadOnlySpanLike[Int32]) -> Int32:
    return len(span(x))

def span_sum(x: ReadOnlySpanLike[Int32]) -> Int32:
    total: Int32 = 0
    for v in span(x):
        total += v
    return total

def main() -> None:
    # span() on user type through protocol param
    buf = Buffer()
    print(span_len(buf))
    print(span_sum(buf))

    # span() on builtin types
    items: list[Int32] = [1, 2, 3, 4]
    print(len(span(items)))

    arr: Array[Int32, 3] = [5, 6, 7]
    print(len(span(arr)))

main()
