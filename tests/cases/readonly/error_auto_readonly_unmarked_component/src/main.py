# In an @auto_readonly result, only the parts marked auto_readonly[...] follow
# the receiver: returning own storage at an unmarked part of a mixed result is
# rejected in the const clone, and the fix names the marker, not the clone's
# readonly projection. A plain method: as an `@overload` set, each stub's
# copy returns its stub's type, whose marker covers the part it returns, so
# that form compiles (calls/overload_as_dispatch, "auto_readonly set").
from tpy import int32, Span, auto_readonly


class Container[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    @auto_readonly
    def pick(self, first: bool) -> T | Span[auto_readonly[T]]:
        if first:
            return self._data[0:1]
        return self._data[0]  # tpyc: error(/at a mutable part of return type '.*' of an @auto_readonly method; mark that part 'auto_readonly\[T\]'/)


def main() -> None:
    c = Container[int32]()
    print(len(c._data))


main()
