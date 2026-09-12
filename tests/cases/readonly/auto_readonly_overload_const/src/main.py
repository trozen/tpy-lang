# @overload + @auto_readonly called on a readonly[T] receiver.
# Verifies that the implementation is not appended to the overload list
# when stubs already have mixed is_readonly (mutable + const clones).
from typing import overload
from tpy import int32, Span, readonly, auto_readonly


class Container[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def add(self, item: T) -> None:
        self._data.append(item)

    @overload
    @auto_readonly
    def __getitem__(self, index: int32) -> T: ...

    @overload
    @auto_readonly
    def __getitem__(self, index: slice) -> Span[auto_readonly[T]]: ...

    @auto_readonly
    def __getitem__(self, index: int32 | slice) -> T | Span[auto_readonly[T]]:
        if isinstance(index, slice):
            s_start = index.start
            s_stop = index.stop
            start: int32 = s_start if s_start is not None else int32(0)
            stop: int32 = s_stop if s_stop is not None else int32(len(self._data))
            return self._data[start:stop]
        else:
            return self._data[index]


def read_container(c: readonly[Container[int32]]) -> None:
    x = c[int32(0)]    # tpyc: type(int32)
    s = c[int32(0):int32(2)]  # tpyc: type(Span[readonly[int32]])
    print(x)
    print(s[int32(0)])
    print(s[int32(1)])


def main() -> None:
    c = Container[int32]()
    c.add(int32(10))
    c.add(int32(20))
    c.add(int32(30))

    x = c[int32(1)]    # tpyc: type(int32)
    s = c[int32(0):int32(2)]  # tpyc: type(Span[int32])
    print(x)
    print(s[int32(0)])

    read_container(c)


main()
