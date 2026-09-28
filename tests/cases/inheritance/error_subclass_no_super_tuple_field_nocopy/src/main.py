# No super() over an `__init__`-less base with a tuple of @nocopy+__del__
# (no default ctor) is rejected (LANGUAGE_FEATURES "Single class inheritance").
from tpy import Ptr, int32, nocopy
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop


@nocopy
class Resource:
    _ptr: Ptr[int32]

    def __init__(self, value: int32) -> None:
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self) -> None:
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)


class Base:
    pair: tuple[Resource, Resource]


class Child(Base):
    label: str

    def __init__(self, a: int32, b: int32, label: str) -> None:   # tpyc: error(/cannot be constructed without arguments \(field 'pair' .*tuple element 0/)
        self.pair = (Resource(a), Resource(b))
        self.label = label


def main() -> None:
    c = Child(1, 2, "x")
    print(c.label)


main()
