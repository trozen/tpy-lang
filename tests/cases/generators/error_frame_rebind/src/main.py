# A name holding a generator is bound once: a rebind to a new generator is an
# error, since something may still reach the old one (BUGS.md#generator-rebind-rejects).
# A clobber warning of an earlier rebind site in the same body is still
# reported beside it.
from typing import Iterator

from tpy import int32


def counter(start: int32) -> Iterator[int32]:
    i = start
    while True:
        i += 1
        yield i


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def main() -> None:
    b = Box(0)
    for i in range(2):
        held = b
        b = Box(3 + i)  # tpyc: warning(/'held' will not keep the object it was given/)
        print(held.n)
    g = counter(0)
    print(first(g))
    # The subject: g already holds a generator.
    g = counter(100)  # tpyc: error(/cannot rebind 'g' to a new generator: a name holding a generator is bound once/)
    print(first(g))


main()
