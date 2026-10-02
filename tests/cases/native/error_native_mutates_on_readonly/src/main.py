# A method stub cannot declare an element write and also that it writes
# nothing: mutates="elements" beside @readonly is rejected.
from typing import Iterator
from tpy import int32, NativeIterable, readonly, pure
from tpy.extern import native


@native("my::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    # The subject: a readonly accessor that claims to replace elements.
    @native("at", mutates="elements")  # tpyc: error(/mutates=...\) on 'at': the method declares that it does not write its receiver/)
    @readonly
    def at(self, i: int32) -> T: ...


def first(r: Ring[int32]) -> int32:
    return r.at(0)
