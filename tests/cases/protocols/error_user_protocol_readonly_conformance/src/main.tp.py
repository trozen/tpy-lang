# User protocol with @readonly method -- record without readonly fails conformance.
from tpy import Int32, readonly
from typing import Protocol


class Readable(Protocol):
    @readonly
    def read(self) -> Int32:
        ...


class BadReader:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def read(self) -> Int32:
        return self.value


def use_readable(r: Readable) -> Int32:
    return r.read()


b = BadReader(42)
print(use_readable(b))  # tpyc: error(/does not conform/)
