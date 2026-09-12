# User protocol with @readonly methods -- correct implementation passes
# and concept uses const T&.
from tpy import int32, readonly
from typing import Protocol


class Readable(Protocol):
    @readonly
    def read(self) -> int32:
        ...


class GoodReader:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def read(self) -> int32:
        return self.value


def use_readable(r: Readable) -> int32:
    return r.read()


g = GoodReader(42)
print(use_readable(g))
