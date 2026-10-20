# A live or repeated tuple NAME inside a tuple literal warns each owned leaf it
# copies by its full path: return, async / method / nested return, arg, yield.
import asyncio
from typing import Iterator
from tpy import Own, int32


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


def bump(a: A) -> None:
    a.x = 5


# return: the repeated member copies, so the two slots hold two objects.
def ret_rep() -> tuple[tuple[Own[A], int32], tuple[Own[A], int32]]:
    t = (A(1), 2)
    return (t, t)  # tpyc: warning(/tuple element 0\.0/) warning(/tuple element 1\.0/)


# async return: the same rule as a plain return.
async def async_rep() -> tuple[tuple[Own[A], int32], tuple[Own[A], int32]]:
    t = (A(1), 2)
    return (t, t)  # tpyc: warning(/tuple element 0\.0/) warning(/tuple element 1\.0/)


class Maker:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # method return: the same rule as a plain return. A call of a method
    # returning a nested owned tuple does not lower today
    # (BUGS.md#nested-own-tuple-result-consumers-reject), so the return is
    # pinned by its warnings only.
    def rep(self) -> tuple[tuple[Own[A], int32], tuple[Own[A], int32]]:
        t = (A(1), 2)
        return (t, t)  # tpyc: warning(/tuple element 0\.0/) warning(/tuple element 1\.0/)


# nested: a member two levels down names its whole path. A local of the
# result's type does not lower today
# (BUGS.md#nested-own-tuple-result-consumers-reject), so it is pinned by its
# warnings only.
def nested_rep() -> tuple[tuple[tuple[Own[A], int32], int32], tuple[Own[A], int32]]:
    t = (A(1), 2)
    return ((t, 1), t)  # tpyc: warning(/tuple element 0\.0\.0/) warning(/tuple element 1\.0/)


# An `Own` marker a level down (`v: tuple[tuple[Own[A], int32], int32]`) has
# no literal-argument render today
# (BUGS.md#own-marked-nested-member-arg-literal-rejects), so that argument is
# pinned by a sema unit test (test_tuple_source_ownership.py).
def own_member(p: tuple[Own[tuple[A, int32]], int32]) -> None:
    w = p[0]
    bump(w[0])


# The yielded nested tuple has no consumer that lowers today
# (BUGS.md#nested-own-tuple-result-consumers-reject), so the yields are pinned
# by their warnings only.
def yld_live() -> Iterator[tuple[tuple[Own[A], int32], int32]]:
    t = (A(1), 2)
    yield (t, 3)  # tpyc: warning(/tuple element 0\.0/)
    print(t[1])


def yld_rep() -> Iterator[tuple[tuple[Own[A], int32], tuple[Own[A], int32]]]:
    t = (A(1), 2)
    yield (t, t)  # tpyc: warning(/tuple element 0\.0/) warning(/tuple element 1\.0/)


def main() -> None:
    # Each section bumps the object in slot 0 and reads slot 1: CPython
    # shares one object between them, TPy holds the two copies it warned.
    r = ret_rep()
    u = r[0]
    bump(u[0])
    print("ret", r[1][0].x)
    ra = asyncio.run(async_rep())
    ua = ra[0]
    bump(ua[0])
    print("async", ra[1][0].x)
    t = (A(1), 2)
    # arg, `Own`-marked tuple member: the live member copies.
    own_member((t, 3))  # tpyc: warning(/tuple element 0\.0/)
    print("own arg", t[0].x)


main()
