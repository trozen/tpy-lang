# Generic @dynamic protocol: structural conformance + vtable dispatch.
# Container[T] is generic; Box and Ratio both have `get() -> int32`
# and `set(val: int32) -> None` structurally (no inheritance). Used as
# Container[int32] both as a local (Adapter slot) and as a function
# parameter (RefAdapter wrap). The set(val: T) method exercises
# TypeParamRef in method *parameter* position (virtual-method param
# rendering via `::tpy::param_val_or_ref_t<T>` in both base and override).
from typing import Protocol
from tpy import int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...

    def set(self, val: T) -> None:
        ...


class Box:
    v: int32

    def __init__(self, v: int32):
        self.v = v

    def get(self) -> int32:
        return self.v

    def set(self, val: int32) -> None:
        self.v = val


class Ratio:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def get(self) -> int32:
        return self.n

    def set(self, val: int32) -> None:
        self.n = val


def show(c: Container[int32]) -> None:
    print(c.get())


def bump(c: Container[int32]) -> None:
    c.set(c.get() + 1)


def main() -> None:
    c: Container[int32] = Box(7)
    print(c.get())
    bump(c)
    print(c.get())
    c = Ratio(42)
    print(c.get())
    show(Box(100))
    r = Ratio(99)
    show(r)
    bump(r)
    show(r)


main()
