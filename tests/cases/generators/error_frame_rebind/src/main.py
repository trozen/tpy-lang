# A name holding a generator is bound once: a rebind to a new generator is an
# error, since something may still reach the old one (BUGS.md#generator-rebind-rejects).
from typing import Iterator

from tpy import int32


def counter(start: int32) -> Iterator[int32]:
    i = start
    while True:
        i += 1
        yield i


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def main() -> None:
    g = counter(0)
    print(first(g))
    # The subject: g already holds a generator.
    g = counter(100)  # tpyc: error(/cannot rebind 'g' to a new generator: a name holding a generator is bound once/)
    print(first(g))


main()
