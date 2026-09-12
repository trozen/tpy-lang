# Explicit auto_readonly[T] return type annotation:
# compiler generates dual overloads using annotated const return type.
from tpy import int32, Span, readonly, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(10), int32(20), int32(30)]

    @auto_readonly
    def as_span(self) -> Span[auto_readonly[int32]]:
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    s = b.as_span()  # tpyc: type(Span[readonly[int32]])
    print(s[int32(0)])
    print(s[int32(1)])


def main() -> None:
    b = Buffer()
    s = b.as_span()  # tpyc: type(Span[int32])
    print(s[int32(2)])
    read_buf(b)


main()
