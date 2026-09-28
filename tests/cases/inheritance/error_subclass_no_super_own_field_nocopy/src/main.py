# No super(), though an owned value is moved into the inherited field of a
# base with no default ctor (LANGUAGE_FEATURES "Single class inheritance").
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


class Child(Base):
    label: str

    def __init__(self, r: Own[Resource], label: str) -> None:   # tpyc: error(/cannot be constructed without arguments \(field 'res' has type 'Resource'/)
        self.res = r
        self.label = label


def main() -> None:
    c = Child(Resource(7), "x")
    print(c.label)


main()
