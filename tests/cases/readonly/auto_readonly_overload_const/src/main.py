# @overload + @auto_readonly called on a readonly[T] receiver.
# Verifies that the implementation is not appended to the overload list
# when stubs already have mixed is_readonly (mutable + const clones).
from typing import overload
from tpy import Int32, Span, readonly, auto_readonly


class Container[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def add(self, item: T) -> None:
        self._data.append(item)

    @overload
    @auto_readonly
    def __getitem__(self, index: Int32) -> T: ...

    @overload
    @auto_readonly
    def __getitem__(self, index: slice) -> Span[auto_readonly[T]]: ...

    @auto_readonly
    def __getitem__(self, index: Int32 | slice) -> T | Span[auto_readonly[T]]:
        if isinstance(index, slice):
            s_start = index.start
            s_stop = index.stop
            start: Int32 = s_start if s_start is not None else Int32(0)
            stop: Int32 = s_stop if s_stop is not None else Int32(len(self._data))
            return self._data[start:stop]
        else:
            return self._data[index]


def read_container(c: readonly[Container[Int32]]) -> None:
    x = c[Int32(0)]    # tpyc: type(Int32)
    s = c[Int32(0):Int32(2)]  # tpyc: type(Span[readonly[Int32]])
    print(x)
    print(s[Int32(0)])
    print(s[Int32(1)])


def main() -> None:
    c = Container[Int32]()
    c.add(Int32(10))
    c.add(Int32(20))
    c.add(Int32(30))

    x = c[Int32(1)]    # tpyc: type(Int32)
    s = c[Int32(0):Int32(2)]  # tpyc: type(Span[Int32])
    print(x)
    print(s[Int32(0)])

    read_container(c)


main()
