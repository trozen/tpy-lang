# Test ReadOnlySpanLike[T] protocol: parameter typing, for-loop iteration, coercion.
from tpy import Int32, Span, ReadOnlySpan, ReadOnlySpanLike

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [1, 2, 3]

    def __span__(self) -> Span[Int32]:
        return self._data

def sum_span(c: ReadOnlySpanLike[Int32]) -> Int32:
    total: Int32 = 0
    for x in c:
        total += x
    return total

def accept_ro(s: ReadOnlySpan[Int32]) -> Int32:
    total: Int32 = 0
    for x in s:
        total += x
    return total

def test_pass_to_ro_span(c: ReadOnlySpanLike[Int32]) -> Int32:
    """ReadOnlySpanLike coerces to ReadOnlySpan."""
    return accept_ro(c)

def main() -> None:
    print(sum_span(Buffer()))
    print(test_pass_to_ro_span(Buffer()))
    print("done")

main()
