# Overload-probe LHS-hint seed propagation. An overloaded generic function
# `wrap[T](Own[Box[T]]) -> Own[Rc[Box[T]]]` (sibling overload taking str)
# is called with a nested generic-ctor arg `Box(Dog(...))` under LHS hint
# `Rc[Box[Pet]]`. Without the seeded probe, overload pre-analysis would
# cache Box's inner T=Dog and the post-selection retry can't refresh it
# (cache short-circuit), producing C++ that mixes Box<Dog> into a
# Box<Pet> slot. With the seed, the inner Box first-pass-analyzes under
# hint Box[Pet] and codegen emits Box<Pet>(Adapter<Pet, Dog>(...)).
from typing import Protocol, overload
from tpy import dynamic, Own, StrView
from tplib import Box, Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> StrView:
        return self.label


@overload
def wrap[T](x: Own[Box[T]]) -> Own[Rc[Box[T]]]:
    return Rc.new(x)


@overload
def wrap(x: str) -> Own[Rc[Box[str]]]:
    return Rc.new(Box(x))


def main() -> None:
    r: Rc[Box[Pet]] = wrap(Box(Dog("Rex")))  # tpyc: type(Rc[Box[Pet]])
    print(r.get().get().name())


main()
