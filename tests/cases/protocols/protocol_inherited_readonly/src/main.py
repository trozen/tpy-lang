# Inherited @readonly protocol methods work on readonly receivers.
# Base defines @readonly read(), Child inherits it.
# Calling read() on readonly[Child] and on T: Child must succeed.
from tpy import Int32, readonly
from typing import Protocol


class Base(Protocol):
    @readonly
    def read(self) -> Int32:
        ...


class Child(Base, Protocol):
    def write(self, v: Int32) -> None:
        ...


class Impl:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def read(self) -> Int32:
        return self.value

    def write(self, v: Int32) -> None:
        self.value = v


@readonly
def read_via_child(x: Child) -> Int32:
    return x.read()


def read_via_bound[T: Child](x: readonly[T]) -> Int32:
    return x.read()


obj = Impl(42)
print(read_via_child(obj))
print(read_via_bound(obj))
