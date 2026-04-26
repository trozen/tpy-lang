# Self passed as a positional arg to a generic free function whose body mutates
# its parameter. Auto-readonly inference must NOT mark the calling method as
# readonly: mutation propagates through the -1 sentinel in param_map.
from typing import Protocol
from tpy import Int32


class Bumpable(Protocol):
    def bump(self) -> None: ...


def trigger[T: Bumpable](x: T) -> None:
    x.bump()


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n = self.n + 1

    def step(self) -> None:
        trigger(self)


def main() -> None:
    c = Counter()
    c.step()
    c.step()
    print(c.n)


main()
