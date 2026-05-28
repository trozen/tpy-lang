# `return` inside a suspending `finally` ends iteration. Expected: 1, 2,
# then StopIteration.
from typing import Iterator


def gen() -> Iterator[int]:
    try:
        yield 1
    finally:
        yield 2
        return


def main() -> None:
    for v in gen():
        print(v)


main()
