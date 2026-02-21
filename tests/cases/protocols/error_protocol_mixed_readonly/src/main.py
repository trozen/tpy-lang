# Calling a non-readonly protocol method in a @readonly context is an error.
from tpy import Int32, readonly
from typing import Protocol


class Mixed(Protocol):
    @readonly
    def read(self) -> Int32:
        ...

    def write(self, v: Int32) -> None:
        ...


@readonly
def bad(m: Mixed) -> None:
    m.write(10)  # tpyc: error(/non-readonly/)
