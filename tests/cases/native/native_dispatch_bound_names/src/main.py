# A @native class's bindings render wherever it is reached: a subscript
# through `__getitem__`, members through a Ptr, @cpp_template record args.
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Iterator
import asyncio
from tpy import int32, Own, NativeIterable, Ptr, ValueType, readonly
from tpy.extern import native, cpp_template


@native("nb::Item")
class Item:
    n: int32


@native("nb::Bag")
class MemberBag:
    @native("at")
    def __getitem__(self, k: int32) -> Item: ...


@native("nb::Bag")
class TemplateBag:
    @cpp_template("{self}.at({0})")
    def __getitem__(self, k: int32) -> Item: ...


@native("nb::Bag")
class FunctionBag:
    @native("nb::bag_get", function=True)
    def __getitem__(self, k: int32) -> Item: ...


@native("nb::Bag")
class MergeBag:
    def __init__(self) -> None: ...

    @native("at")
    def __getitem__(self, k: int32) -> Item: ...

    @cpp_template("{self}.merge({0})")
    def merge(self, o: MergeBag) -> None: ...


# --- template method: a same-record argument
def merged(b: MergeBag, o: MergeBag) -> int32:
    b.merge(o)  # tpyc: ok
    return b[0].n


@native("nb::Kind")
class Kind(ValueType):
    pass


@native("nb::second_kind")
def second_kind() -> Kind: ...


@native("nb::Bag")
class KindBag:
    def __init__(self) -> None: ...

    @cpp_template("{self}.at(static_cast<int32_t>({0}))")
    def by_kind(self, k: Kind) -> Item: ...


@native("nb::Root")
class Root:
    def __init__(self) -> None: ...

    @native("top")
    @property
    def Top(self) -> Item: ...

    @native("pick")
    @readonly
    def pick(self, i: int32) -> int32: ...

    @native("bump")
    def bump(self) -> None: ...


class Holder:
    root: Ptr[Root]

    def __init__(self, root: Ptr[Root]) -> None:
        self.root = root

    # --- through a Ptr: method, a Ptr FIELD receiver
    def top_n(self) -> int32:
        return self.root.Top.n  # tpyc: ok

    def picked(self) -> int32:
        return self.root.pick(2)  # tpyc: ok


# --- through a Ptr: free function, a Ptr parameter and a Ptr field off a parameter
def via_ptr(p: Ptr[Root], h: Holder) -> int32:
    return p.pick(1) + h.root.Top.n  # tpyc: ok nullable(p)


# --- through a Ptr proven non-null: the arrow spelling
def via_proven(p: Ptr[Root]) -> int32:
    if p is not None:
        return p.pick(3)  # tpyc: ok non_null(p)
    return 0


# --- template method: a @native value-type (C++ enum) argument
def by_kind(b: KindBag, k: Kind) -> int32:
    return b.by_kind(k).n  # tpyc: ok


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@native("nb::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("at")
    @readonly
    def __getitem__(self, i: int32) -> T: ...

    @native("push")
    def push(self, v: Own[T]) -> None: ...


# --- subscript: free function
def free_member(b: MemberBag, k: int32) -> int32:
    return b[k].n  # tpyc: ok


def free_template(b: TemplateBag, k: int32) -> int32:
    return b[k].n  # tpyc: ok


def free_function(b: FunctionBag, k: int32) -> int32:
    return b[k].n  # tpyc: ok


# An element-typed (`elements=True`) generic receiver binds the same way.
def free_elements(r: Ring[Cell], k: int32) -> int32:
    return r[k].n  # tpyc: ok


# --- subscript: method
class Reader:
    base: int32

    def __init__(self) -> None:
        self.base = 100

    def member(self, b: MemberBag, k: int32) -> int32:
        return self.base + b[k].n  # tpyc: ok

    def template(self, b: TemplateBag, k: int32) -> int32:
        return self.base + b[k].n  # tpyc: ok

    def function(self, b: FunctionBag, k: int32) -> int32:
        return self.base + b[k].n  # tpyc: ok


# --- subscript: generator body
def gen_member(b: MemberBag) -> Iterator[int32]:
    for k in range(3):
        yield b[k].n  # tpyc: ok


def gen_template(b: TemplateBag) -> Iterator[int32]:
    for k in range(3):
        yield b[k].n  # tpyc: ok


def gen_function(b: FunctionBag) -> Iterator[int32]:
    for k in range(3):
        yield b[k].n  # tpyc: ok


# --- subscript: async body
async def async_member(b: MemberBag, k: int32) -> int32:
    await asyncio.sleep(0)
    return b[k].n  # tpyc: ok


async def async_template(b: TemplateBag, k: int32) -> int32:
    await asyncio.sleep(0)
    return b[k].n  # tpyc: ok


async def async_function(b: FunctionBag, k: int32) -> int32:
    await asyncio.sleep(0)
    return b[k].n  # tpyc: ok


# --- subscript: comprehension
def comp_member(b: MemberBag) -> Own[list[int32]]:
    return [b[k].n for k in range(3)]  # tpyc: ok


def comp_template(b: TemplateBag) -> Own[list[int32]]:
    return [b[k].n for k in range(3)]  # tpyc: ok


def comp_function(b: FunctionBag) -> Own[list[int32]]:
    return [b[k].n for k in range(3)]  # tpyc: ok


# --- subscript: returning the record
def ret_member(b: MemberBag, k: int32) -> Item:
    return b[k]  # tpyc: ok


def ret_template(b: TemplateBag, k: int32) -> Item:
    return b[k]  # tpyc: ok


def ret_function(b: FunctionBag, k: int32) -> Item:
    return b[k]  # tpyc: ok


def main() -> None:
    mb = MemberBag()
    tb = TemplateBag()
    fb = FunctionBag()
    print("free:", free_member(mb, 0), free_template(tb, 1), free_function(fb, 2))
    ring = Ring[Cell]()
    ring.push(Cell(7))
    print("free elements:", free_elements(ring, 0))
    r = Reader()
    print("method:", r.member(mb, 0), r.template(tb, 1), r.function(fb, 2))
    print("gen:", list(gen_member(mb)), list(gen_template(tb)), list(gen_function(fb)))
    print("async:", asyncio.run(async_member(mb, 0)),
          asyncio.run(async_template(tb, 1)), asyncio.run(async_function(fb, 2)))
    print("comp:", comp_member(mb), comp_template(tb), comp_function(fb))
    # The returned record aliases the bag's element: a write through it is
    # seen by the next subscript read.
    ret_member(mb, 0).n = 11
    ret_template(tb, 1).n = 21
    ret_function(fb, 2).n = 31
    print("ret:", ret_member(mb, 0).n, ret_template(tb, 1).n, ret_function(fb, 2).n)
    rt = Root()
    h = Holder(rt)
    print("ptr:", h.top_n(), h.picked(), via_ptr(rt, h))
    # The Ptr aliases rt: a write through rt is seen through the field.
    rt.bump()
    print("ptr after:", h.top_n(), h.picked())
    kb = KindBag()
    k = second_kind()
    print("template arg:", by_kind(kb, k))
    print("ptr proven:", via_proven(rt))
    a = MergeBag()
    o = MergeBag()
    # merge writes into `a`; the next subscript read sees the sum.
    print("merged:", merged(a, o))


main()
