# Test Spannable[T] protocol: parameter typing, for-loop iteration, coercion.
from tpy import int32, Span, Spannable, readonly, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [1, 2, 3]

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[int32]]:
        return self._data

def sum_span(c: Spannable[int32]) -> int32:
    total: int32 = 0
    for x in c:
        total += x
    return total

def accept_ro(s: Span[readonly[int32]]) -> int32:
    total: int32 = 0
    for x in s:
        total += x
    return total

def test_pass_to_ro_span(c: Spannable[int32]) -> int32:
    """Spannable coerces to Span[readonly[T]]."""
    return accept_ro(c)

def main() -> None:
    print(sum_span(Buffer()))
    print(test_pass_to_ro_span(Buffer()))
    print("done")

main()
