# Test __span__() protocol for zero-cost iteration via range-based for.
# Covers mutable/readonly returns and @readonly parameter iteration.
from tpy import Int32, Span, ReadOnlySpan
from tpy import readonly

class MutBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30]

    def __span__(self) -> Span[Int32]:
        return self._data

class ROBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [40, 50, 60]

    def __span__(self) -> ReadOnlySpan[Int32]:
        return self._data

def test_mut() -> None:
    buf = MutBuffer()
    for x in buf:
        print(x)

def test_ro() -> None:
    buf = ROBuffer()
    for x in buf:
        print(x)

def test_iterate_twice() -> None:
    buf = MutBuffer()
    total: Int32 = 0
    for x in buf:
        total += x
    for x in buf:
        total += x
    print(total)

def test_readonly_param(buf: readonly[MutBuffer]) -> None:
    total: Int32 = 0
    for x in buf:
        total += x
    print(total)

def test_rvalue_span() -> None:
    # Iterate over a temporary -- the container must stay alive for the span
    total: Int32 = 0
    for x in MutBuffer():
        total += x
    print(total)

test_mut()
test_ro()
test_iterate_twice()
test_readonly_param(MutBuffer())
test_rvalue_span()
print("done")
