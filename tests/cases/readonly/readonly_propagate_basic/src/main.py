# @readonly_propagate: method returns Span/Ptr/reference, adapts based on receiver constness.
# Tests explicit annotation on custom methods.
from tpy import Int32, Span, ReadOnlySpan, Ptr, ReadOnlyPtr, readonly, readonly_propagate

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(1), Int32(2), Int32(3)]

    @readonly_propagate
    def as_span(self) -> Span[readonly_propagate[Int32]]:
        return self._data

    # __getitem__ is implicitly readonly; returns Int32 (value type), no dual overload needed
    def __getitem__(self, index: Int32) -> Int32:
        return self._data[index]


def read_buf(b: readonly[Buffer]) -> None:
    # Calling @readonly_propagate method on readonly receiver -> ReadOnlySpan
    s = b.as_span()  # tpyc: type(ReadOnlySpan[Int32])
    print(s[Int32(0)])
    print(s[Int32(1)])


def main() -> None:
    b = Buffer()

    # Mutable receiver -> Span[Int32]
    s = b.as_span()  # tpyc: type(Span[Int32])
    print(s[Int32(2)])

    read_buf(b)
    print(b[Int32(0)])


main()
