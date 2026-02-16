# User protocol with @readonly methods -- correct implementation passes
# and concept uses const T&.
from tpy import Int32, readonly
from typing import Protocol


class Readable(Protocol):
    @readonly
    def read(self) -> Int32:
        ...


class GoodReader:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def read(self) -> Int32:
        return self.value


def use_readable(r: Readable) -> Int32:
    return r.read()


g = GoodReader(42)
print(use_readable(g))
