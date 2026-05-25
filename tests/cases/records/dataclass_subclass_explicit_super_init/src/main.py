# Happy-path escape hatch from the macro-aware super-init error: when a
# @dataclass child inherits from a non-dataclass parent whose default
# ctor is implicitly deleted, the user can write an explicit __init__
# that calls super().__init__(...). The user-written __init__ is NOT
# macro-generated, so the rule fires with the normal (non-macro) path
# and accepts the explicit super() call.
from dataclasses import dataclass
from tpy import Ptr, Int32, nocopy
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
    res: Resource

    def __init__(self, v: Int32) -> None:
        self.res = Resource(v)


@dataclass
class Child(Base):
    extra: Int32

    def __init__(self, v: Int32, extra: Int32) -> None:
        super().__init__(v)
        self.extra = extra


def main() -> None:
    c = Child(42, 7)
    print(c.extra)


main()
