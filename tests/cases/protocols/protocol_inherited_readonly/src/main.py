# Inherited @readonly protocol methods work on readonly receivers.
# Base defines @readonly read(), Child inherits it.
# Calling read() on readonly[Child] and on T: Child must succeed.
from tpy import int32, readonly
from typing import Protocol


class Base(Protocol):
    @readonly
    def read(self) -> int32:
        ...


class Child(Base, Protocol):
    def write(self, v: int32) -> None:
        ...


class Impl:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def read(self) -> int32:
        return self.value

    def write(self, v: int32) -> None:
        self.value = v


@readonly
def read_via_child(x: Child) -> int32:
    return x.read()


def read_via_bound[T: Child](x: readonly[T]) -> int32:
    return x.read()


obj = Impl(42)
print(read_via_child(obj))
print(read_via_bound(obj))
