# No super() over an `__init__`-less base whose @nocopy+__del__ field has no
# default ctor is rejected (LANGUAGE_FEATURES "Single class inheritance").
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
    res: Resource


class Child(Base):
    extra: int32

    def __init__(self, v: int32, e: int32) -> None:   # tpyc: error(/cannot be constructed without arguments \(field 'res' has type 'Resource' \('Resource' has '__del__'/)
        self.res = Resource(v)
        self.extra = e


def main() -> None:
    c = Child(42, 7)
    print(c.extra)


main()
