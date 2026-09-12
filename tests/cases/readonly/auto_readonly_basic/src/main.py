# @auto_readonly: method returns Span/Ptr/reference, adapts based on receiver constness.
# Tests explicit annotation on custom methods.
from tpy import int32, Span, Ptr, readonly, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(1), int32(2), int32(3)]

    @auto_readonly
    def as_span(self) -> Span[auto_readonly[int32]]:
        return self._data

    # __getitem__ is implicitly readonly; returns int32 (value type), no dual overload needed
    def __getitem__(self, index: int32) -> int32:
        return self._data[index]


def read_buf(b: readonly[Buffer]) -> None:
    # Calling @auto_readonly method on readonly receiver -> Span[readonly[T]]
    s = b.as_span()  # tpyc: type(Span[readonly[int32]])
    print(s[int32(0)])
    print(s[int32(1)])


def main() -> None:
    b = Buffer()

    # Mutable receiver -> Span[int32]
    s = b.as_span()  # tpyc: type(Span[int32])
    print(s[int32(2)])

    read_buf(b)
    print(b[int32(0)])


main()
