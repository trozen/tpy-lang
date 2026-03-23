# @auto_readonly decorator with non-self params: all eligible params become
# readonly in the const overload (decorator wraps everything).
from tpy import Int32, Span, readonly, auto_readonly

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(10), Int32(20), Int32(30)]

    @auto_readonly
    def merge_span(self, other: list[Int32]) -> Span[auto_readonly[Int32]]:
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    items: list[Int32] = [Int32(99)]
    s = b.merge_span(items)  # tpyc: type(Span[readonly[Int32]])
    print(s[Int32(0)])


def main() -> None:
    b = Buffer()
    items: list[Int32] = [Int32(99)]
    s = b.merge_span(items)  # tpyc: type(Span[Int32])
    print(s[Int32(0)])
    read_buf(b)


main()
