# The super-init rule must accept both `super().__init__(...)` AND the
# explicit `Base.__init__(self, ...)` form. Here the base's C++ default
# ctor is deleted (Resource has @nocopy+__del__), so the rule's
# precondition holds; the rule must NOT fire because the unbound-self
# call satisfies the base-init requirement.
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

    def __init__(self, v: int32, e: int32) -> None:
        Base.__init__(self, v)   # explicit base-init form (no super())
        self.extra = e


def main() -> None:
    c = Child(42, 7)
    print(c.extra)


main()
