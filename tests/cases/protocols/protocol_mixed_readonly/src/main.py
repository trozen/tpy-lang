# Protocol with mixed readonly/non-readonly methods.
# read() is @readonly, write() is not. In a @readonly context only read() is callable.
from tpy import int32, readonly
from typing import Protocol


class Mixed(Protocol):
    @readonly
    def read(self) -> int32:
        ...

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
def safe_read(m: Mixed) -> int32:
    return m.read()


def use_both(m: Mixed) -> int32:
    m.write(10)
    return m.read()


obj = Impl(42)
print(safe_read(obj))
print(use_both(obj))
