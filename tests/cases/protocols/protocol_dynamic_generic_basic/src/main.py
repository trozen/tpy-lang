# Generic @dynamic protocol: structural conformance + vtable dispatch.
# Container[T] is generic; Box and Ratio both have `get() -> Int32`
# and `set(val: Int32) -> None` structurally (no inheritance). Used as
# Container[Int32] both as a local (Adapter slot) and as a function
# parameter (RefAdapter wrap). The set(val: T) method exercises
# TypeParamRef in method *parameter* position (virtual-method param
# rendering via `::tpy::param_val_or_ref_t<T>` in both base and override).
from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...

    def set(self, val: T) -> None:
        ...


class Box:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v

    def get(self) -> Int32:
        return self.v

    def set(self, val: Int32) -> None:
        self.v = val


class Ratio:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def get(self) -> Int32:
        return self.n

    def set(self, val: Int32) -> None:
        self.n = val


def show(c: Container[Int32]) -> None:
    print(c.get())


def bump(c: Container[Int32]) -> None:
    c.set(c.get() + 1)


def main() -> None:
    c: Container[Int32] = Box(7)
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
