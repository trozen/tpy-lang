# Test: protocol union as a method parameter on a class
from typing import Protocol


class Measurable(Protocol):
    def measure(self) -> int: ...

class Walkable(Protocol):
    def walk(self) -> int: ...


class Processor:
    count: int

    def __init__(self) -> None:
        self.count = 0

    def process(self, items: Measurable | Walkable) -> None:
        if isinstance(items, Measurable):
            self.count = items.measure()
        elif isinstance(items, Walkable):
            self.count = items.walk()


class Ruler:
    def measure(self) -> int:
        return 5

class Walker:
    def walk(self) -> int:
        return 99


def main() -> None:
    p = Processor()
    r = Ruler()
    p.process(r)
    print(p.count)

    w = Walker()
    p.process(w)
    print(p.count)


main()
