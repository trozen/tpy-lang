# What stays a named local: a fresh argument a generator's frame keeps past
# the statement, the copy an owned parameter takes of a live object, and a
# `*args` pack's backing array.
from typing import Iterator
from tpy import int32, Own
from fixtures import Bag, mk


def walk(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def keep(xs: Own[list[int32]]) -> Own[list[int32]]:
    xs.append(0)
    return xs


def count(*vals: int32) -> int32:
    return len(vals)


def own_copy(b: Bag) -> None:
    # Own copy: the callee's own copy of a parameter's field, held then moved
    # in -- a warned copy (CPython hands `b.xs` itself on), so `b.xs` is not
    # printed
    zs = keep(b.xs)  # tpyc: warning(/copies/)
    zs.append(9)
    print("own copy", zs)


def main() -> None:
    # frame-kept: the generator holds its argument past the statement
    g = walk(mk())  # tpyc: ok
    print("frame kept", list(g))
    # a fresh argument the generator frame keeps, through a ternary
    print("frame ternary", list(walk(mk() if len(mk()) > 1 else [7])))  # tpyc: ok
    own_copy(Bag([5, 6]))
    # *args: the pack's backing array
    print("varargs", count(1, 2, len(mk())))
