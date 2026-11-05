"""A field write (`=` or `+=`) into the NEW object a call returns is a sema
error: the object is dropped after the statement. Writes that reach existing
storage -- through a reference result, a pointer, a property setter, a
borrowed await result -- are not."""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from ..thir.testutil import _compile

HEADER = """\
import asyncio
from typing import Protocol
from tpy import int32, Own, Ptr, Span, take_ptr, deref
from tpy.extern import native, native_field
from tplib.rc import Rc


class Base:
    base_n: int32

    def __init__(self) -> None:
        self.base_n = 0


class Item(Base):
    n: int32

    def __init__(self, n: int32) -> None:
        super().__init__()
        self.n = n


class HasN(Protocol):
    n: int32


class Box[T]:
    x: T

    def __init__(self, x: T) -> None:
        self.x = x

    def get(self) -> T:
        return self.x


class Picker:
    p: Ptr[Item]

    def __init__(self, it: Item) -> None:
        self.p = take_ptr(it)

    def __getitem__(self, i: int32) -> Item:
        p = self.p
        return deref(p)


class Holder:
    item: Item
    items: list[Item]

    def __init__(self, n: int32) -> None:
        self.item = Item(n)
        self.items = [Item(n)]

    @property
    def fresh(self) -> Own[Item]:
        return Item(self.item.n)

    @property
    def held(self) -> Item:
        return self.item

    @property
    def k(self) -> int32:
        return self.item.n

    @k.setter
    def k(self, v: int32) -> None:
        self.item.n = v

    def mk(self, n: int32) -> Own[Holder]:
        return Holder(n)

    def get(self) -> Item:
        return self.item

    def view(self) -> Span[Item]:
        return self.items

    def pair(self) -> tuple[Item, Item]:
        return (self.item, self.item)


class Bag:
    items: list[Item]

    def __init__(self, it: Item) -> None:
        self.items = [it]


class PBox[T]:
    x: T

    def __init__(self, x: T) -> None:
        self.x = x


class Dynamic:
    def __init__(self) -> None:
        pass

    def __getattr__(self, name: str) -> Own[Item]:
        return Item(1)


@native("nb::NItem")
class NItem:
    n: int32 = native_field("n_")


@native("nb::make_nitem")
def make_nitem() -> Own[NItem]: ...


G = Holder(1)


def mk_bag(it: Item) -> Own[Bag]:
    return Bag(it)


def mk_pbox[T](x: T) -> Own[PBox[T]]:
    return PBox(x)


def pair(it: Item) -> tuple[Item, Item]:
    return (it, it)


def make() -> Own[Item]:
    return Item(1)


def ptr() -> Ptr[Holder]:
    return take_ptr(G)


def make_h() -> Own[Holder]:
    return Holder(1)


async def amake() -> Own[Item]:
    return Item(1)


async def aget(h: Holder) -> Item:
    return h.item
"""

_MESSAGE = "is a new object dropped after this statement"


@pytest.mark.parametrize("body, named", [
    # the augmented spelling
    ("def f() -> None:\n    make().n += 5\n", "'make(...)'"),
    # a field chain held by value climbs to the call
    ("def f(h: Holder) -> None:\n    h.mk(1).item.n = 5\n", "'mk(...)'"),
    # a property getter's new object names the property
    ("def f(h: Holder) -> None:\n    h.fresh.n = 5\n", "'fresh'"),
    # an awaited new object
    ("async def f() -> None:\n    (await amake()).n = 5\n", "'amake(...)'"),
    # an inherited field of a new object
    ("def f() -> None:\n    make().base_n = 5\n", "'make(...)'"),
    # a select whose every arm is a new object
    ("def f(c: bool) -> None:\n    (make() if c else make()).n = 5\n",
     "the expression"),
    # an awaited pending coroutine bound to a name: the result is still new
    ("async def f() -> None:\n    pending = amake()\n"
     "    (await pending).n = 5\n", "the expression"),
    # a new object a @native binding returns
    ("def f() -> None:\n    make_nitem().n = 5\n", "'make_nitem(...)'"),
    # an open `T` whose bound needs the field: no handle satisfies it, so
    # the box holds `x` inline
    ("def g[T: HasN](x: T) -> None:\n    mk_pbox(x).x.n = 7\n",
     "'mk_pbox(...)'"),
    # a `__getattr__` returning by value hands back a new object
    ("def f(d: Dynamic) -> None:\n    d.fresh.n = 5\n", "the expression"),
])
def test_write_into_new_object_is_an_error(body: str, named: str) -> None:
    with pytest.raises(SemanticError) as err:
        _compile(HEADER + body)
    assert f"the result of {named} {_MESSAGE}" in str(err.value), \
        str(err.value)


@pytest.mark.parametrize("body", [
    # a reference result writes the existing item
    "def f(h: Holder) -> None:\n    h.get().n = 5\n",
    # a member read through a pointer lives in the pointee
    "def f() -> None:\n    ptr().item.n = 5\n",
    # a property getter lending existing storage
    "def f(h: Holder) -> None:\n    h.held.n = 5\n",
    # a borrowed await result
    "async def f(h: Holder) -> None:\n    (await aget(h)).n = 5\n",
    # a Span view over existing storage (the lowering refuses it)
    "def f(h: Holder) -> None:\n    h.view()[0].n = 5\n",
    # a tuple of references (the lowering refuses it)
    "def f(h: Holder) -> None:\n    h.pair()[0].n = 5\n",
    # an open `T` result in a generic body (the lowering refuses it)
    "def f[T: HasN](b: Box[T]) -> None:\n    b.get().n = 7\n",
    # a subscript getter handing back an existing pointee (the lowering
    # refuses the subscript spelling)
    "def f(it: Item) -> None:\n    Picker(it)[0].n = 5\n",
    # a property setter on a new object
    "def f() -> None:\n    make_h().k = 5\n",
    # an element of a new object's container (the lowering refuses it,
    # BUGS.md#field-write-through-call-handle-rejects)
    "def f(it: Item) -> None:\n    mk_bag(it).items[0].n = 5\n",
    # an Rc clone's payload is the shared object, reached through
    # `__deref__`
    "def f(r: Rc[Item]) -> None:\n    r.clone().n = 5\n",
    "def f(r: Rc[Item]) -> None:\n    r.clone().n += 5\n",
    # the lent element of a tuple of references, at either index spelling
    "def f(it: Item) -> None:\n    pair(it)[1].n = 5\n",
    "def f(it: Item) -> None:\n    pair(it)[-1].n = 5\n",
])
def test_write_reaching_existing_storage_is_not(body: str) -> None:
    _compile(HEADER + body)
