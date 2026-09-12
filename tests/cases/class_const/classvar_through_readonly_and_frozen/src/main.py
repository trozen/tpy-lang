# `obj.X = v` on a mutable ClassVar writes to class-scoped storage, never
# to the instance. So a readonly receiver and a frozen dataclass instance
# both let the write through -- the instance isn't being mutated.
from typing import ClassVar
from tpy import int32, readonly
from dataclasses import dataclass


class Counter:
    instances: ClassVar[int32] = 0

    def __init__(self) -> None:
        pass


@dataclass(frozen=True)
class FrozenCounter:
    name: str
    instances: ClassVar[int32] = 0


def bump(c: readonly[Counter]) -> None:
    c.instances += 1  # tpyc: warning(/Assigning to ClassVar 'Counter.instances' via instance/)


def main() -> None:
    Counter.instances = 0
    a = Counter()
    bump(a)
    bump(a)
    print(Counter.instances)

    FrozenCounter.instances = 0
    f = FrozenCounter("widget")
    f.instances = 5  # tpyc: warning(/Assigning to ClassVar 'FrozenCounter.instances' via instance/)
    print(FrozenCounter.instances)


main()
