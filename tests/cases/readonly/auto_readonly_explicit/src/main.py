# Explicit auto_readonly[T] return type annotation:
# compiler generates dual overloads using annotated const return type.
from tpy import Int32, Span, readonly, auto_readonly

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(10), Int32(20), Int32(30)]

    @auto_readonly
    def as_span(self) -> Span[auto_readonly[Int32]]:
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    s = b.as_span()  # tpyc: type(Span[readonly[Int32]])
    print(s[Int32(0)])
    print(s[Int32(1)])


def main() -> None:
    b = Buffer()
    s = b.as_span()  # tpyc: type(Span[Int32])
    print(s[Int32(2)])
    read_buf(b)


main()
