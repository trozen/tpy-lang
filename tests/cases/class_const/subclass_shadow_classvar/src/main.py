# Phase 8: a subclass may shadow a parent's non-final ClassVar with the
# same finality and type. Each declaring class gets its own `static inline`
# slot, so Child.X and Parent.X resolve to independent storage -- matching
# Python's per-`__dict__` shadowing.
from typing import ClassVar
from tpy import int32


class Parent:
    counter: ClassVar[int32] = 0


class Child(Parent):
    counter: ClassVar[int32] = 100


def main() -> None:
    Parent.counter = 1
    Child.counter = 2
    print(Parent.counter)
    print(Child.counter)
    Child.counter += 5
    print(Parent.counter)
    print(Child.counter)


main()
