# Generic record with @overload __getitem__ for both index and slice.
# Tests type parameter substitution in slice return type.
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
    def __getitem__(self, index: int32) -> T: ...  # tpyc: ok

    @overload
    def __getitem__(self, index: slice) -> Span[readonly[T]]: ...  # tpyc: ok

    def __getitem__(self, index: int32 | slice) -> T | Span[readonly[T]]:
        if isinstance(index, slice):
            s_start = index.start
            s_stop = index.stop
            start: int32 = s_start if s_start is not None else int32(0)
            stop: int32 = s_stop if s_stop is not None else int32(len(self._data))
            return self._data[start:stop]
        else:
            return self._data[index]

def main() -> None:
    c = Container[int32]()
    c.add(int32(10))
    c.add(int32(20))
    c.add(int32(30))

    # Index
    print(c[int32(1)])

    # Slice
    sp = c[int32(0):int32(2)]
    for x in sp:
        print(x)

    # String container
    s = Container[str]()
    s.add("hello")
    s.add("world")
    print(s[int32(0)])

main()
