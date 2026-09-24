# A name holds one generator function's objects: holding another's would
# need type erasure, which allocates (docs/LANGUAGE_FEATURES.md, Generators).
from typing import Iterator

from tpy import int32


def counter(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        i += 1
        yield i


def evens(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        i += 2
        yield i


def main(c: bool) -> None:
    g = counter(6)
    if c:
        # The subject: a different generator function under the same name.
        g = evens(6)  # tpyc: error(/cannot rebind 'g' to a different generator: 'g' holds a 'counter' generator/)
    for v in g:
        print(v)


main(True)
