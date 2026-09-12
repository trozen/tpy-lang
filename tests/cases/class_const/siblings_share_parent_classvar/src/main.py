# Two child classes sharing a parent's mutable ClassVar see each other's
# writes -- no shadow, single class-scoped storage at the parent.
from typing import ClassVar
from tpy import int32


class Parent:
    counter: ClassVar[int32] = 0


class ChildA(Parent):
    pass


class ChildB(Parent):
    pass


def main() -> None:
    ChildA.counter = 7
    print(Parent.counter)
    print(ChildB.counter)
    ChildB.counter += 3
    print(ChildA.counter)
    print(Parent.counter)


main()
