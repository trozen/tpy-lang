# In an @auto_readonly result, only the parts marked auto_readonly[...] follow
# the receiver: returning own storage at an unmarked part of a mixed result is
# rejected in the const clone, and the fix names the marker, not the clone's
# readonly projection.
from typing import overload
from tpy import int32, Span, auto_readonly


class Container[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    @overload
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @overload
    @auto_readonly
    def __getitem__(self, index: slice) -> Span[auto_readonly[T]]: ...

    @auto_readonly
    def __getitem__(self, index: int32 | slice) -> T | Span[auto_readonly[T]]:
        if isinstance(index, slice):
            return self._data[0:1]
        return self._data[index]  # tpyc: error(/at a mutable part of return type '.*' of an @auto_readonly method; mark that part 'auto_readonly\[T\]'/)


def main() -> None:
    c = Container[int32]()
    print(len(c._data))


main()
