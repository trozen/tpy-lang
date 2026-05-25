# Method sibling of overload_nested_generic_ctor.
#
# Overloaded generic methods aren't supported in TPy, so the analogue uses
# a generic record `Wrapper[T]` with non-generic overloaded methods whose
# param/return types pick up T from the receiver. Calling
# `w.wrap(Box(Dog(...)))` on `w: Wrapper[Pet]` under LHS `Rc[Box[Pet]]`
# exercises the methods.py:344 probe path: each substituted overload's
# return type matches the LHS hint shape, and the methods probe-seeder
# should thread `Box[Pet]` to the inner Box's first-pass analysis.
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


class Wrapper[T]:
    def __init__(self) -> None:
        pass

    @overload
    def wrap(self, x: Own[Box[T]]) -> Own[Rc[Box[T]]]:
        return Rc.new(x)

    @overload
    def wrap(self, x: str) -> Own[Rc[Box[str]]]:
        return Rc.new(Box(x))


def main() -> None:
    w = Wrapper[Pet]()
    r: Rc[Box[Pet]] = w.wrap(Box(Dog("Buddy")))  # tpyc: type(Rc[Box[Pet]])
    print(r.get().get().name())


main()
