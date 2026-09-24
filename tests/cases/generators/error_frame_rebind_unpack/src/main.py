# A tuple unpack binds its target to a temporary, so it does not rebind a name
# holding a generator (docs/LANGUAGE_FEATURES.md, Generators).
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
    # The subject: the unpack would leave the old generator open.
    g, k = counter(100), 5  # tpyc: error(/cannot rebind generator 'g' in a tuple unpack/)
    print(first(g), k)


main()
