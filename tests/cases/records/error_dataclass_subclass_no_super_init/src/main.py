# @dataclass subclass of a non-dataclass parent whose default ctor is
# implicitly deleted: the macro synthesizes Child.__init__ without
# super(), and the synthesized __init__ would leave Base uninitialized.
# The sema rule must reject with a macro-aware message that doesn't
# blame the user for code they didn't write.
from dataclasses import dataclass
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


@dataclass
class Child(Base):                    # tpyc: error(/synthesized by '@dataclass' for 'Child' does not initialize parent class 'Base'/)
    extra: int32


def main() -> None:
    c = Child(42, 7)
    print(c.extra)


main()
