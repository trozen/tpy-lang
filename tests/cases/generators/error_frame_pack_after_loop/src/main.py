# A `*args` generator bound in a loop and read after it is refused: the pack
# it keeps is built by each pass (docs/LANGUAGE_FEATURES.md, Generators;
# a pack naming one root is not: BUGS.md#frame-pack-temp-rooted-at-element).
from typing import Iterator

from tpy import int32


def packs(*xss: list[int32]) -> Iterator[int32]:
    for xs in xss:
        for x in xs:
            yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer(a: list[int32], b: list[int32]) -> int32:
    for i in range(2):
        g = packs(a, b)
    # The subject: `g` is read after the loop that binds it.
    return first(g)  # tpyc: error(/cannot use 'g' after the loop it is bound in: it borrows a temporary/)


print(outer([1, 2], [3]))
