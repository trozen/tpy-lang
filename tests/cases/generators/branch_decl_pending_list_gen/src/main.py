# A list literal first declared inside an if/else branch of a sync
# generator resolves in the frame field, surviving the yield.
from typing import Iterator


def gen(c: bool) -> Iterator[int]:
    if c:
        xs = [1, 2]
    else:
        xs = [3]
    yield len(xs)
    yield xs[0]


def main() -> None:
    for v in gen(True):
        print(v)
    for v in gen(False):
        print(v)


main()
