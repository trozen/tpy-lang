# Three-level shadow chain: Child -> Parent -> Grandparent each declares
# the same ClassVar. Phase 8's BFS-with-discard validates Child against
# the nearest declaring ancestor (Parent) only -- not against Grandparent.
from typing import ClassVar
from tpy import int32


class Grandparent:
    counter: ClassVar[int32] = 0


class Parent(Grandparent):
    counter: ClassVar[int32] = 0


class Child(Parent):
    counter: ClassVar[int32] = 0


def main() -> None:
    Grandparent.counter = 1
    Parent.counter = 2
    Child.counter = 3
    print(Grandparent.counter)
    print(Parent.counter)
    print(Child.counter)


main()
