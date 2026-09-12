# TupleType branch coverage for the super-init rule. The base's field
# is a tuple whose element type is a @nocopy+__del__ record; the
# predicate's TupleType arm in `_field_type_blocks_default_ctor` recurses
# into the element type to determine that the tuple cannot default-init,
# so Base() = default is implicitly deleted, and Child without super()
# must be rejected.
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

    def __init__(self, a: int32, b: int32) -> None:
        self.pair = (Resource(a), Resource(b))


class Child(Base):
    label: str

    def __init__(self, a: int32, b: int32, label: str) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.label = label


def main() -> None:
    c = Child(1, 2, "x")
    print(c.label)


main()
