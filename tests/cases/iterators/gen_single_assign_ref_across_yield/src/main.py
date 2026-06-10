# Single-assign reference alias held across a yield: `a` aliases a live list
# element and must survive as a `Box*` frame field, so mutating it between
# yields is visible through the source list (generator sibling of the
# single-assign async case).
from typing import Iterator


class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def g(items: list[Box]) -> Iterator[int]:
    a = items[0]
    yield a.n
    a.n += 10
    yield a.n


def main() -> None:
    items = [Box(1)]
    for v in g(items):
        print(v)
    print(items[0].n)


main()
