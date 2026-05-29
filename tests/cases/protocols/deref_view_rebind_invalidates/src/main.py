# Reassigning an owning-wrapper local inside an isinstance-deref branch must
# invalidate the deref-view narrowing: after `b = Box(Cat())`, `b.name()` must
# resolve against the new payload, not the stale `Dog` the branch narrowed to
# (a stale fact would emit a dynamic_cast<Dog*> on the Cat payload -> nullptr
# deref). Regression guard for the deref_view_key invalidation in
# update_after_write.
from typing import Protocol
from tpy import dynamic
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def f() -> str:
    b: Box[Pet] = Box(Dog())
    if isinstance(b, Dog):
        b = Box(Cat())           # rebind drops the deref-view narrowing
        return b.name()          # resolves against Cat, not stale Dog
    return "unreachable"


def main() -> None:
    print(f())


main()
