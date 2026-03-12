# Explicit readonly_alt[T] return type annotation:
# compiler generates dual overloads using annotated const return type.
from tpy import Int32, Span, ReadOnlySpan, readonly, readonly_alt

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(10), Int32(20), Int32(30)]

    @readonly_alt
    def as_span(self) -> Span[readonly_alt[Int32]]:
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    s = b.as_span()  # tpyc: type(ReadOnlySpan[Int32])
    print(s[Int32(0)])
    print(s[Int32(1)])


def main() -> None:
    b = Buffer()
    s = b.as_span()  # tpyc: type(Span[Int32])
    print(s[Int32(2)])
    read_buf(b)


main()
