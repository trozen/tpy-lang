# Test SpanIter-based __iter__() iteration for user types.
# Covers mutable/readonly span access, @readonly parameters, and rvalue containers.
from tpy import int32, Span, SpanIter
from tpy import readonly, auto_readonly

class MutBuffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30]

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[int32]]:
        return self._data

    @auto_readonly
    def __iter__(self) -> SpanIter[auto_readonly[int32]]:
        return SpanIter(self.__span__())

class ROBuffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [40, 50, 60]

    def __span__(self) -> Span[readonly[int32]]:
        return self._data

    @readonly
    def __iter__(self) -> SpanIter[readonly[int32]]:
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
    total: int32 = 0
    for x in buf:
        total += x
    for x in buf:
        total += x
    print(total)

def test_readonly_param(buf: readonly[MutBuffer]) -> None:
    total: int32 = 0
    for x in buf:
        total += x
    print(total)

def test_rvalue_span() -> None:
    # Iterate over a temporary -- the container must stay alive for the span
    total: int32 = 0
    for x in MutBuffer():
        total += x
    print(total)

test_mut()
test_ro()
test_iterate_twice()
test_readonly_param(MutBuffer())
test_rvalue_span()
print("done")
