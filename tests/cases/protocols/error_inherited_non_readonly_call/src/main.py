# Non-readonly method inherited from parent protocol is rejected in readonly context.
from tpy import int32, readonly
from typing import Protocol


class Base(Protocol):
    def mutate(self) -> None:
        ...


class Child(Base, Protocol):
    @readonly
    def read(self) -> int32:
        ...


@readonly
def bad(c: Child) -> None:
    c.mutate()  # tpyc: error(/non-readonly/)
