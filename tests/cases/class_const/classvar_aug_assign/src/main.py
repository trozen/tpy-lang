# Aug-assign on a mutable ClassVar updates the class-scoped storage in place.
from typing import ClassVar
from tpy import Int32


class Counter:
    instances: ClassVar[Int32] = 0

    def __init__(self) -> None:
        Counter.instances += 1


def main() -> None:
    Counter.instances = 0
    Counter()
    Counter()
    Counter()
    Counter.instances += 7
    print(Counter.instances)


main()
