# Regression guard: a generic @dynamic protocol whose method returns
# `Own[Proto[T]]` requires `template<typename T> struct Proto;` to be
# forward-declared before the concept lowers the unique_ptr<Proto<T>>
# constraint. Exercises the type_params branch of the forward-decl emit.
from typing import Protocol
from tpy import Int32, dynamic, readonly, Own
from tplib import Box


@dynamic
class Cloneable[T](Protocol):
    @readonly
    def replicate(self) -> Own[Cloneable[T]]: ...
    @readonly
    def value(self) -> T: ...


class IntBox(Cloneable[Int32]):
    v: Int32

    def __init__(self, v: Int32):
        self.v = v

    @readonly
    def replicate(self) -> Own[Cloneable[Int32]]:
        return IntBox(self.v)

    @readonly
    def value(self) -> Int32:
        return self.v


def main() -> None:
    c: Cloneable[Int32] = IntBox(7)
    b = Box(c.replicate())
    print(b.value())


main()
