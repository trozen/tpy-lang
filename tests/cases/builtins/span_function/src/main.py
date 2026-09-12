# Test tpy.span(): get Span[readonly[T]] from Spannable types.
from tpy import int32, Span, Spannable, Array, span, auto_readonly

class Buffer:
    _data: list[int32]
    def __init__(self) -> None:
        self._data = [10, 20, 30]
    @auto_readonly
    def __span__(self) -> Span[auto_readonly[int32]]:
        return self._data

def span_len(x: Spannable[int32]) -> int32:
    return len(span(x))

def span_sum(x: Spannable[int32]) -> int32:
    total: int32 = 0
    for v in span(x):
        total += v
    return total

def main() -> None:
    # span() on user type through protocol param
    buf = Buffer()
    print(span_len(buf))
    print(span_sum(buf))

    # span() on builtin types
    items: list[int32] = [1, 2, 3, 4]
    print(len(span(items)))

    arr: Array[int32, 3] = [5, 6, 7]
    print(len(span(arr)))

main()
