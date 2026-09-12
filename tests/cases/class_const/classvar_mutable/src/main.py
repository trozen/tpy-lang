# PEP 526 ClassVar[T] = value: mutable class-scoped storage.
# Reads and writes via ClassName.X go to the same `static inline` slot.
from typing import ClassVar
from tpy import int32


class Counter:
    instances: ClassVar[int32] = 0

    def __init__(self) -> None:
        Counter.instances += 1


def main() -> None:
    print(Counter.instances)
    a = Counter()
    b = Counter()
    c = Counter()
    print(Counter.instances)
    Counter.instances = 0
    print(Counter.instances)


main()
