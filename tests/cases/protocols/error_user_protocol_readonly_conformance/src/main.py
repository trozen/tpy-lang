# User protocol with @readonly method -- record without readonly fails conformance.
from tpy import int32, readonly
from typing import Protocol


class Readable(Protocol):
    @readonly
    def read(self) -> int32:
        ...


class BadReader:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def read(self) -> int32:
        return self.value


def use_readable(r: Readable) -> int32:
    return r.read()


b = BadReader(42)
print(use_readable(b))  # tpyc: error(/does not conform/)
