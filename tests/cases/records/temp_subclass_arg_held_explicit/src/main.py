# An explicit type argument at a generic call binds a base parameter: the
# subclass temporary held for it is declared at its own type, not sliced.
from typing import Protocol
from tpy import dynamic


@dynamic
class Surface(Protocol):
    def hit(self) -> str: ...


class Mat(Surface):
    hits: int

    def __init__(self) -> None:
        self.hits = 0

    def hit(self) -> str:
        self.hits += 1
        return "mat" + str(self.hits)


class Chrome(Mat):
    def hit(self) -> str:
        self.hits += 1
        return "chrome" + str(self.hits)


def two[U: Surface](a: U, b: U) -> str:
    return a.hit() + " " + b.hit()


def main() -> None:
    print("generic_explicit", two[Mat](Mat(), Chrome()))  # tpyc: ok


main()
