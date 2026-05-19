# Regression guard: an @dynamic protocol method whose return type mentions
# the protocol itself (e.g. `clone(self) -> Own[Self]`) requires a forward
# declaration of the abstract base struct *before* the concept that lowers
# the return type. Pre-fix the concept's `std::convertible_to<std::unique_ptr
# <Cloneable>>` constraint referenced `Cloneable` before its struct existed,
# breaking C++ compilation.
from typing import Protocol
from tpy import dynamic, readonly, Own
from tplib import Box


@dynamic
class Cloneable(Protocol):
    @readonly
    def replicate(self) -> Own[Cloneable]: ...
    @readonly
    def name(self) -> str: ...


class Dog(Cloneable):
    @readonly
    def replicate(self) -> Own[Cloneable]:
        return Dog()
    @readonly
    def name(self) -> str:
        return "dog"


def main() -> None:
    d: Cloneable = Dog()
    b = Box(d.replicate())
    print(b.name())


main()
