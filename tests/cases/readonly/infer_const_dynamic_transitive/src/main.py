# Transitive @dynamic inheritance: Child -> Parent -> @dynamic HasValue.
# Child's value() is two hops from the @dynamic protocol, but it overrides
# the same C++ pure virtual slot Parent declared. _dynamic_proto_requires_nonconst
# walks the MRO via iter_dynamic_protocols to find HasValue from Parent's
# implemented_protocols and pin both methods non-const. Without that walk,
# Child.value() would auto-const-infer (body only reads self) and emit a
# const signature that fails to override Parent's non-const virtual.
from tpy import int32, dynamic
from typing import Protocol


@dynamic
class HasValue(Protocol):
    def value(self) -> int32: ...

    def bump(self) -> None: ...


class Parent(HasValue):
    _n: int32

    def __init__(self, n: int32) -> None:
        self._n = n

    def value(self) -> int32:           # direct override -- must NOT be const
        return self._n

    def bump(self) -> None:
        self._n += 1


class Child(Parent):
    def __init__(self, n: int32) -> None:
        Parent.__init__(self, n)

    def value(self) -> int32:           # transitive override -- must NOT be const
        return self._n * 2


def show(v: HasValue) -> None:
    print(v.value())


def grow(v: HasValue) -> None:
    v.bump()


def main() -> None:
    show(Parent(7))
    show(Child(7))
    c = Child(7)
    # A named Child binds the protocol parameter as itself: the bump is seen.
    grow(c)
    print(c.value())


main()
