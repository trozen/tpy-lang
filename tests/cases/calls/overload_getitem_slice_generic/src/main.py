# Generic record with @overload __getitem__ for both index and slice.
# Tests type parameter substitution in slice return type.
from typing import overload
from tpy import Int32, Span, readonly, readonly_alt

class Container[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def add(self, item: T) -> None:
        self._data.append(item)

    @overload
    @readonly_alt
    def __getitem__(self, index: Int32) -> T: ...  # tpyc: ok

    @overload
    def __getitem__(self, index: slice) -> Span[readonly[T]]: ...  # tpyc: ok

    def __getitem__(self, index: Int32 | slice) -> T | Span[readonly[T]]:
        if isinstance(index, slice):
            s_start = index.start
            s_stop = index.stop
            start: Int32 = s_start if s_start is not None else Int32(0)
            stop: Int32 = s_stop if s_stop is not None else Int32(len(self._data))
            return self._data[start:stop]
        else:
            return self._data[index]

def main() -> None:
    c = Container[Int32]()
    c.add(Int32(10))
    c.add(Int32(20))
    c.add(Int32(30))

    # Index
    print(c[Int32(1)])

    # Slice
    sp = c[Int32(0):Int32(2)]
    for x in sp:
        print(x)

    # String container
    s = Container[str]()
    s.add("hello")
    s.add("world")
    print(s[Int32(0)])

main()
