# Per-param auto_readonly: self: auto_readonly[Self] gives finer control
# than @auto_readonly decorator -- only marked params become readonly in const overload.
from typing import Self
from tpy import Int32, Span, readonly, auto_readonly

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(1), Int32(2), Int32(3)]

    # Per-param: only self is auto_readonly; dest stays mutable in both overloads.
    def copy_into(self: auto_readonly[Self], dest: list[Int32]) -> Span[auto_readonly[Int32]]:
        for i in range(len(self._data)):
            dest.append(self._data[i])
        return self._data


def read_buf(b: readonly[Buffer]) -> None:
    out: list[Int32] = []
    s = b.copy_into(out)  # tpyc: type(Span[readonly[Int32]])
    print(len(out))
    print(s[Int32(0)])


def main() -> None:
    b = Buffer()
    out: list[Int32] = []
    s = b.copy_into(out)  # tpyc: type(Span[Int32])
    print(len(out))
    print(s[Int32(0)])
    read_buf(b)


main()
