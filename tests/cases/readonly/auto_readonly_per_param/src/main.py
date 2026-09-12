# Per-param auto_readonly: self: auto_readonly[Self] gives finer control
# than @auto_readonly decorator -- only marked params become readonly in const overload.
from typing import Self
from tpy import int32, Span, readonly, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(1), int32(2), int32(3)]

    # Per-param: only self is auto_readonly; dest stays mutable in both overloads.
    def copy_into(self: auto_readonly[Self], dest: list[int32]) -> Span[auto_readonly[int32]]:
        for i in range(len(self._data)):
            dest.append(self._data[i])
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    out: list[int32] = []
    s = b.copy_into(out)  # tpyc: type(Span[readonly[int32]])
    print(len(out))
    print(s[int32(0)])


def main() -> None:
    b = Buffer()
    out: list[int32] = []
    s = b.copy_into(out)  # tpyc: type(Span[int32])
    print(len(out))
    print(s[int32(0)])
    read_buf(b)


main()
