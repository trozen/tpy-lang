# Array branch of `_field_type_blocks_default_ctor`: a base with
# `Array[Resource, N]` field where Resource is @nocopy+__del__. The
# array's element type has no default ctor, so std::array<Resource, N>
# is not default-ctorable and the base's = default ctor is implicitly
# deleted -- subclass without super() must be rejected.
from tpy import Array, Ptr, Int32, nocopy
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop


@nocopy
class Resource:
    _ptr: Ptr[Int32]

    def __init__(self, value: Int32) -> None:
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self) -> None:
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)


class Base:
    arr: Array[Resource, 2]

    def __init__(self, a: Int32, b: Int32) -> None:
        self.arr = Array[Resource, 2](Resource(a), Resource(b))


class Child(Base):
    label: str

    def __init__(self, a: Int32, b: Int32, label: str) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.label = label


def main() -> None:
    c = Child(1, 2, "x")
    print(c.label)


main()
