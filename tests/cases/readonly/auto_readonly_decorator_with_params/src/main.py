# @auto_readonly decorator with non-self params: all eligible params become
# readonly in the const overload (decorator wraps everything).
from tpy import int32, Span, readonly, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(10), int32(20), int32(30)]

    @auto_readonly
    def merge_span(self, other: list[int32]) -> Span[auto_readonly[int32]]:
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    items: list[int32] = [int32(99)]
    s = b.merge_span(items)  # tpyc: type(Span[readonly[int32]])
    print(s[int32(0)])


def main() -> None:
    b = Buffer()
    items: list[int32] = [int32(99)]
    s = b.merge_span(items)  # tpyc: type(Span[int32])
    print(s[int32(0)])
    read_buf(b)


main()
