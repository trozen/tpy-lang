"""A match subject binds by reference only when it is existing storage with a
durable root; a temporary's storage, a lent tuple element and a call over a
non-place argument stay refused."""

from __future__ import annotations

import pytest

from .testutil import _strict_reject

HEADER = """\
from tpy import int32
from tplib import Box


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[Item]

    def __init__(self) -> None:
        self.items = [Item(0), Item(0)]

    def get(self, i: int32) -> Item:
        return self.items[i]


def first(c: Bag) -> Item:
    return c.items[0]


def pair(it: Item) -> tuple[Item, Item]:
    return (it, it)


def k(x: Item) -> int32:
    return x.n


def f(a: Item, b: Item, it: Item) -> None:
    match {subject}:
        case Item() as m:
            m.n = 5
"""


@pytest.mark.parametrize("subject", [
    # an element of a temporary receiver
    "Bag().get(0)",
    # a reference result whose argument is a temporary
    "first(Bag())",
    # the payload of a temporary handle
    "Box(Item(3)).get()",
    # a call whose argument is no storage (a function)
    "min(a, b, key=k)",
    # an element a call's tuple result lends
    "pair(it)[0]",
])
def test_subject_without_a_durable_root_is_refused(subject: str) -> None:
    err, _fallback = _strict_reject(HEADER.format(subject=subject) + "\n")
    assert err.message.endswith("(stmt.match)"), err.message
