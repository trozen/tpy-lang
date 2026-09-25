# A class name where a Callable/Fn is expected stands for `lambda *a: C(*a)`:
# the slot's parameter types pick the constructor, its return type types the
# construction (`list` takes its element type from the slot).
import asyncio
from enum import Enum
from typing import Callable, Iterator
from tpy import Fn, Own, Send, ValueType, dispatch, int32
from shapes import Cell, Cell as Aliased


class Pt:
    x: int32

    def __init__(self) -> None:
        self.x = 0


class Q:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class Color(Enum):
    RED = 1
    GREEN = 2


class Appender:
    n: int

    def __init__(self, xs: list[int]) -> None:
        xs.append(7)
        self.n = len(xs)


def apply(f: Fn[[str], int], s: str) -> int:
    return f(s)


def fresh(f: Callable[[], list[int]]) -> Own[list[int]]:
    xs = f()
    xs.append(1)
    return xs


def make_all(f: Fn[[int32], Q], n: int32) -> int32:
    total = 0
    for i in range(n):
        total += f(i).v
    return total


def maybe(f: Callable[[], int] | None) -> int:
    if f is None:
        return -1
    return f()


def sendy(f: Send[Callable[[], int]]) -> int:
    return f()


@dispatch
def conv(f: Callable[[str], int], s: str) -> int:
    return f(s)


@dispatch
def conv(f: Callable[[int], str], s: int) -> str:
    return f(s)


def ret_factory() -> Callable[[], Pt]:
    return Pt  # tpyc: ok


class Registry:
    make: Callable[[], dict[str, list[int]]]

    def __init__(self) -> None:
        self.make = dict  # tpyc: ok

    def run(self, f: Callable[[], set[str]]) -> int:
        s = f()
        s.add("a")
        return len(s)


class Counter:
    n: int

    def __init__(self, f: Callable[[], int]) -> None:
        self.n = f() + 1


MOD: Callable[[], list[str]] = list  # tpyc: ok


def gen() -> Iterator[int]:
    f: Callable[[str], int] = int  # tpyc: ok
    yield f("7")


async def co() -> int:
    return apply(int, "9")  # tpyc: ok


def params() -> None:
    print("params", apply(int, "4"), make_all(Q, 3), maybe(int), maybe(None), sendy(int))  # tpyc: ok
    xs = fresh(list)  # tpyc: ok
    xs.append(2)
    print("params list", xs)


def locals_() -> None:
    p: Callable[[], Pt] = Pt  # tpyc: ok
    a = p()
    b = p()
    a.x = 5
    print("local record", a.x, b.x)
    s: Callable[[], str] = str  # tpyc: ok
    n: Callable[[], int32] = int  # tpyc: ok
    fl: Callable[[str], float] = float  # tpyc: ok
    print("local value", len(s()), n(), fl("2.5"))
    q: Callable[[int32], Q] = Q  # tpyc: ok
    print("local ctor arg", q(6).v)
    c: Callable[[int], Color] = Color  # tpyc: ok
    print("local enum", c(2).name)
    bx: Callable[[int32], Box[int32]] = Box  # tpyc: ok
    print("local generic", bx(4).v)
    # An Optional or union result slot takes the construction as a member.
    # Not called: its result is read in the wrong form,
    # BUGS.md#callable-optional-record-result-borrow-form.
    fo: Callable[[], Pt | None] = Pt  # tpyc: ok
    fu: Callable[[str], int32 | str] = str  # tpyc: ok
    u = fu("u")
    print("local optional", u)


def ref_arg() -> None:
    # The constructor mutates the caller's list: the factory passes it through.
    f: Callable[[list[int]], Appender] = Appender  # tpyc: ok
    xs = [1]
    print("ref arg", f(xs).n, xs)
    lol: list[list[int]] = [[1], [2]]
    print("ref arg map", [a.n for a in map(Appender, lol)], lol)  # tpyc: ok


def builtins_() -> None:
    print("map", list(map(int, ["3", "1", "2"])))  # tpyc: ok
    print("sorted", sorted([3, 1, 20], key=str))  # tpyc: ok
    print("map list", [len(x) for x in map(list, ["ab", "c"])])  # tpyc: ok
    print("map generic", [b.v for b in map(Box, [5, 6])])  # tpyc: ok
    a = "3"
    b = "12"
    print("max key", max(a, b, key=int))  # tpyc: ok
    print("overload", conv(int, "41") + 1, conv(str, 41) + "!")  # tpyc: ok


def method_field_ctor() -> None:
    r = Registry()
    mk = r.make
    d = mk()
    d["k"] = [1]
    d["k"].append(2)
    print("field", d, len(mk()))
    print("method", r.run(set))  # tpyc: ok
    print("ctor", Counter(int).n)  # tpyc: ok


def imported() -> None:
    mk: Callable[[int32], Cell] = Cell  # tpyc: ok
    al: Callable[[int32], Cell] = Aliased  # tpyc: ok
    print("imported", mk(1).n, al(2).n)


def container() -> None:
    # A class name as a container element takes the element's callable type.
    fs: list[Callable[[], list[int]]] = [list, list]  # tpyc: ok
    a = fs[0]()
    a.append(1)
    print("container", a, fs[1]())


def returned() -> None:
    f = ret_factory()
    p = f()
    p.x = 3
    print("return", p.x, f().x)


def module_level() -> None:
    m = MOD()
    m.append("z")
    print("module", m, MOD())


def generator() -> None:
    print("generator", list(gen()))


def comprehension() -> None:
    print("comprehension", [apply(int, s) for s in ["1", "2"]])  # tpyc: ok


def closure() -> None:
    def inner() -> int:
        g: Callable[[str], int] = int  # tpyc: ok
        return g("8")
    print("closure", inner())


def main() -> None:
    params()
    locals_()
    ref_arg()
    builtins_()
    method_field_ctor()
    imported()
    container()
    returned()
    module_level()
    generator()
    print("async", asyncio.run(co()))
    comprehension()
    closure()


main()
