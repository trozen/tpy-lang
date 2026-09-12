# Subclass __init__ that doesn't call super().__init__() must be rejected
# when the base class has a field with no default ctor (here: @nocopy+__del__
# Resource). C++ would otherwise fail to synthesize Base::Base() and produce
# a cryptic error from inside the subclass constructor.
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

    def __init__(self, v: int32) -> None:
        self.res = Resource(v)


class Child(Base):
    extra: int32

    def __init__(self, v: int32, e: int32) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.res = Resource(v)
        self.extra = e


def main() -> None:
    c = Child(42, 7)
    print(c.extra)


main()
