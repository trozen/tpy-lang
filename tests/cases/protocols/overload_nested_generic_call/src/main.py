# Variant of overload_nested_generic_ctor where the inner nested arg is a
# generic-function call (Rc.new) instead of a record constructor. Same
# probe-seed propagation surface, different inner expression shape -- pins
# that the seeded probe path threads the hint through both
# `_analyze_generic_function_call` and `_analyze_record_constructor`.
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
def double_wrap[T](inner: Own[Rc[T]]) -> Own[Box[Rc[T]]]:
    return Box(inner)


@overload
def double_wrap(inner: str) -> Own[Box[Rc[str]]]:
    return Box(Rc.new(inner))


def main() -> None:
    b: Box[Rc[Pet]] = double_wrap(Rc.new(Dog("Spot")))  # tpyc: type(Box[Rc[Pet]])
    print(b.get().get().name())


main()
