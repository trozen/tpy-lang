# `_field_type_blocks_default_ctor`: a base with a `Resource` field where
# Resource is @nocopy+__del__. The field's type controls the enclosing
# default ctor: Resource has __del__ and a required __init__, so Base has
# no default ctor. Subclass without super() must be rejected.
from tpy import Own, Ptr, int32, nocopy
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
