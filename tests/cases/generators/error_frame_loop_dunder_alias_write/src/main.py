# `__for_iteral` is a plain alias: the loop-frame check sees the write through it.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)

from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def ident(xs: list[int32]) -> list[int32]:
    return xs


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer(xs: list[int32]) -> Iterator[int32]:
    __for_iteral = ident(xs)
    i = 0
    while i < 3:
        # The subject: `g` is kept open while the pass grows `xs` through the alias.
        g = items(xs)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'append', which may write '__for_iteral'/)
        yield first(g)
        __for_iteral.append(7)
        i += 1


print(list(outer([1, 2])))
