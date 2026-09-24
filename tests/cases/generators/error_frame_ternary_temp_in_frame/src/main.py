# In a generator body, a generator bound from a ternary may not borrow a
# temporary (docs/LANGUAGE_FEATURES.md, Generators).
from typing import Iterator

from tpy import int32, Own


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def make(base: int32) -> Own[list[int32]]:
    return [base, base + 1]


def outer(c: bool) -> Iterator[int32]:
    # The subject: make(...) dies at the end of the statement.
    g = items(make(1)) if c else items(make(5))  # tpyc: error(/cannot bind generator 'g' from this expression inside a generator or async function: it borrows a temporary/)
    for v in g:
        yield v


print(list(outer(True)))
