# OwnType branch of `_field_type_blocks_default_ctor`: a base with an
# `Own[Resource]` field where Resource is @nocopy+__del__. Storage-form
# Own[T] holds T inline, so the wrapped type's default ctor controls
# the enclosing default ctor. Subclass without super() must be rejected.
from tpy import Own, Ptr, Int32, nocopy
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
    res: Own[Resource]

    def __init__(self, r: Own[Resource]) -> None:
        self.res = r


class Child(Base):
    label: str

    def __init__(self, r: Own[Resource], label: str) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.label = label


def main() -> None:
    c = Child(Resource(7), "x")
    print(c.label)


main()
