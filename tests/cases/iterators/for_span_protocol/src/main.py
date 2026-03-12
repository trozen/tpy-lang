# Test SpanIter-based __iter__() iteration for user types.
# Covers mutable/readonly span access, @readonly parameters, and rvalue containers.
from tpy import Int32, Span, ReadOnlySpan, SpanIter
from tpy import readonly, readonly_alt

class MutBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30]

    @readonly_alt
    def __span__(self) -> Span[readonly_alt[Int32]]:
        return self._data

    @readonly_alt
    def __iter__(self) -> SpanIter[readonly_alt[Int32]]:
        return SpanIter(self.__span__())

class ROBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [40, 50, 60]

    def __span__(self) -> ReadOnlySpan[Int32]:
        return self._data

    @readonly
    def __iter__(self) -> SpanIter[readonly[Int32]]:
        return SpanIter(self.__span__())

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
