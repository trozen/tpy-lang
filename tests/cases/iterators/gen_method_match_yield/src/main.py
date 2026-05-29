# H1: a generator method with a `yield` inside a `match` lowers on the
# resumable frame (the match subject is a field on `self`).
from typing import Iterator


class Counter:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def items(self) -> Iterator[int]:
        match self.n:  # tpyc: ok
            case 0:
                yield 10
                yield 20
            case _:
                yield 30


def main() -> None:
    c = Counter(0)
    for v in c.items():
        print(v)
    print("--")
    c2 = Counter(7)
    for v in c2.items():
        print(v)


main()
