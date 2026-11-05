# A typing.overload set is one @dispatch variant per stub, each the
# implementation's body at its stub's types; sema decides the dead arms.
import asyncio
import itertools
from enum import Enum
from typing import Callable, Final, Iterator, Literal, Protocol, Self, overload

import ovlib
from tpy import (Span, auto_readonly, char, dispatch, dynamic, error_return,
                 int8, int32, int64, ReturnException)


class Square:
    side: float

    def __init__(self, side: float) -> None:
        self.side = side


class Circle:
    r: float

    def __init__(self, r: float) -> None:
        self.r = r


# free function: each copy keeps its own arm
@overload
def area(s: Square) -> float: ...
@overload
def area(s: Circle) -> float: ...
def area(s: Square | Circle) -> float:  # tpyc: ok
    if isinstance(s, Square):
        return s.side * s.side
    return 3.0 * s.r * s.r


# method and static method
class Shop:
    sold: int32

    def __init__(self) -> None:
        self.sold = 0

    @overload
    def sell(self, s: Square) -> str: ...
    @overload
    def sell(self, s: Circle) -> str: ...
    def sell(self, s: Square | Circle) -> str:
        self.sold += 1
        if isinstance(s, Square):
            return "square"
        return "circle"

    @overload
    @staticmethod
    def kind(s: Square) -> str: ...
    @overload
    @staticmethod
    def kind(s: Circle) -> str: ...
    @staticmethod
    def kind(s: Square | Circle) -> str:
        if isinstance(s, Square):
            return "s"
        return "c"


# generic stubs
@overload
def first[T](xs: list[T]) -> T: ...
@overload
def first[T](xs: list[T], i: int32) -> T: ...
def first[T](xs: list[T], i: int32 = 0) -> T:
    return xs[i]


# generic class
class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        # a generic field store warns as everywhere, not per copy
        self.v = v  # tpyc: warning(/may copy T into field/)

    @overload
    def get(self, i: int32) -> T: ...
    @overload
    def get(self, i: str) -> T: ...
    def get(self, i: int32 | str) -> T:
        return self.v


# generator: one frame per stub
@overload
def count(n: int32) -> Iterator[int32]: ...
@overload
def count(n: int32, step: int32) -> Iterator[int32]: ...
def count(n: int32, step: int32 = 1) -> Iterator[int32]:
    i = 0
    while i < n:
        yield i
        i += step


# short stubs: the omitted parameter is a local holding the implementation's
# default -- scalar, None and reference
@overload
def open_mode() -> str: ...
@overload
def open_mode(mode: str) -> str: ...
def open_mode(mode: str = "r") -> str:
    if mode == "r":  # tpyc: ok
        return "read"
    return mode


@overload
def fmt(v: int32) -> str: ...
@overload
def fmt(v: int32, tag: str | None) -> str: ...
def fmt(v: int32, tag: str | None = None) -> str:
    if tag is None:
        tag = "d"  # the None default's local is written, as a parameter would be
    return tag + str(v)


class Tag:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


@overload
def label(v: int32) -> str: ...
@overload
def label(v: int32, tag: Tag | None) -> str: ...
def label(v: int32, tag: Tag | None = None) -> str:
    if tag is None:
        return str(v)
    return tag.name + str(v)


# defaults: an omitted argument takes the implementation's default
@overload
def k(n: int32) -> str: ...
@overload
def k(n: str = "x") -> str: ...
def k(n: int32 | str = "y") -> str:
    if isinstance(n, str):
        return "s:" + n
    return "i"


@overload
def dflt(x: int32, y: int32 = 5) -> int32: ...
@overload
def dflt(x: str, y: int32 = 6) -> int32: ...
def dflt(x: int32 | str, y: int32 = 7) -> int32:
    if isinstance(x, str):
        return 2000 + y
    return 1000 + y


# an omitted argument ranks as the implementation's default: `wide()` runs
# the int32 copy with 5, as CPython does; the str copy does not take 5
@overload
def wide(x: int32) -> str: ...
@overload
def wide(x: str = "s") -> str: ...
def wide(x: int32 | str = 5) -> str:  # tpyc: ok
    if isinstance(x, str):
        return "str " + x
    return "int " + str(x)


# a short stub matches as written: `short(1)` runs the int32 copy, whose
# local `y` holds 7, though only the long stub declares `y`
@overload
def short(x: int32) -> int32: ...
@overload
def short(x: str, y: int32 = 7) -> int32: ...
def short(x: int32 | str, y: int32 = 7) -> int32:  # tpyc: ok
    if isinstance(x, str):
        return y + 1
    return y


# a generic stub's positional-only defaults: `gpo(1)` ranks z's 5 at the
# int32 copy, y's `0` (typed by T) stays its C++ default
@overload
def gpo[T](x: T, y: T = 0, z: int32 = 5, /) -> int32: ...
@overload
def gpo[T](x: T, y: T, z: str, /) -> int32: ...
def gpo[T](x: T, y: T = 0, z: int32 | str = 5, /) -> int32:  # tpyc: ok
    if isinstance(z, str):
        return 9
    return z


# keyword-only default: an omitted keyword takes the implementation's default
@overload
def kw(x: int32, *, y: int32 = 0) -> int32: ...
@overload
def kw(x: str, *, y: int32 = 0) -> int32: ...
def kw(x: int32 | str, *, y: int32 = 7) -> int32:  # tpyc: ok
    if isinstance(x, str):
        return y + 1
    return y


# the implementation's default resolves the set: `st()` runs the
# Literal["r"] copy, though only the Literal["rb"] stub writes a default
@overload
def st(mode: Literal["r"]) -> str: ...
@overload
def st(mode: Literal["rb"] = "rb") -> str: ...
def st(mode: str = "r") -> str:
    if mode == "r":
        return "text:" + mode
    return "binary:" + mode


# a default both copies take ranks as the value passed: `pick()` is `pick("r")`
@overload
def pick(mode: Literal["r"] = "r") -> str: ...
@overload
def pick(mode: str = "r") -> str: ...
def pick(mode: str = "r") -> str:  # tpyc: ok
    return "pick:" + mode


# an omitted keyword-only argument: `kwfill(1)` is `kwfill(1, mode="r")`
@overload
def kwfill(x: int32, *, mode: Literal["rb"] = "rb") -> str: ...
@overload
def kwfill(x: int32, *, mode: str = "r") -> str: ...
def kwfill(x: int32, *, mode: str = "r") -> str:  # tpyc: ok
    return f"{x}:{mode}"


# omitted positional-only arguments, also beside a keyword argument:
# `po()` is `po(1, "r")`
@overload
def po(x: int32 = 1, mode: Literal["r"] = "r", /) -> str: ...
@overload
def po(x: int32, mode: str, /) -> str: ...
def po(x: int32 = 1, mode: str = "r", /) -> str:  # tpyc: ok
    return f"{x}:{mode}"


@overload
def pokw(mode: Literal["r"] = "r", /, *, x: int32 = 1) -> str: ...
@overload
def pokw(mode: str, /, *, x: int32 = 1) -> str: ...
def pokw(mode: str = "r", /, *, x: int32 = 1) -> str:  # tpyc: ok
    return f"{x}:{mode}"


# a method call omitting an argument resolves as the free one does
class Opener:
    opened: int32

    def __init__(self) -> None:
        self.opened = 0

    @overload
    def st(self, mode: Literal["r"]) -> str: ...
    @overload
    def st(self, mode: Literal["rb"] = "rb") -> str: ...
    def st(self, mode: str = "r") -> str:  # tpyc: ok
        self.opened += 1
        return f"{mode}:{self.opened}"


# a Literal stub over a union implementation parameter narrows its member
type Num = int64 | str


@overload
def triple(x: Literal[3]) -> str: ...
@overload
def triple(x: str) -> str: ...
def triple(x: Num) -> str:  # tpyc: ok
    if isinstance(x, str):
        return x * 3
    return str(x * 3000000000)


# a Literal stub holds the implementation's parameter type (here BigInt)
@overload
def power(x: Literal[2]) -> int: ...
@overload
def power(x: int) -> int: ...
def power(x: int) -> int:
    return x ** 70


# Literal[True] / Literal[False] stubs print as the implementation's bool
@overload
def flag(f: Literal[True]) -> str: ...
@overload
def flag(f: Literal[False]) -> str: ...
def flag(f: bool) -> str:
    print("literal bool", f)  # tpyc: ok
    return "t" if f else "f"


def plain_flag(f: bool) -> None:
    print("plain bool", f)


# a short method stub's omitted `Self | None` parameter, and its full stub
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @overload
    def total(self) -> int32: ...
    @overload
    def total(self, other: Self | None) -> int32: ...
    def total(self, other: Self | None = None) -> int32:  # tpyc: ok
        if other is None:
            return self.n
        return self.n + other.n


# the omitted parameter holds the module's N although the body binds its own
N: Final[int32] = 3


# a keyword call to the short copy: `kshort(x=1)` runs it as `kshort(1)`
# does; the long copy needs `y`
@overload
def kshort(x: int32 = 1) -> int32: ...
@overload
def kshort(x: int32, y: str) -> int32: ...
def kshort(x: int32 = 1, y: str = "s") -> int32:  # tpyc: ok
    return x + len(y)


# an implicit dunder call ranks the omitted default as the explicit one:
# `1 in bag` runs the Literal["r"] copy
class Bag:
    @overload
    def __contains__(self, x: int32, mode: Literal["r"] = "r") -> bool: ...
    @overload
    def __contains__(self, x: int32, mode: str = "r") -> bool: ...
    def __contains__(self, x: int32, mode: str = "r") -> bool:  # tpyc: ok
        return mode == "r" and x > 0


# a named default the str copy's type does not take: that copy declares
# no default for `k`
K: Final[int32] = 4


@overload
def named(x: int32, k: int32 = 0) -> str: ...
@overload
def named(x: str, k: str = "") -> str: ...
def named(x: int32 | str, k: int32 | str = K) -> str:  # tpyc: ok
    return f"{x}/{k}"


@overload
def shade(x: int32) -> int32: ...
@overload
def shade(x: int32, k: int32) -> int32: ...
def shade(x: int32, k: int32 = N) -> int32:
    N = 50  # tpyc: ok
    return x + N + k * 1000


@overload
def plain_shade(x: int32) -> int32: ...
@overload
def plain_shade(x: int32, k: int32) -> int32: ...
def plain_shade(x: int32, k: int32 = N) -> int32:
    return x + k * 1000


# polymorphic base: the base stub's isinstance stays a runtime test
@dynamic
class Pet(Protocol):
    def noise(self) -> str:
        ...


class Animal(Pet):
    def noise(self) -> str:
        return "..."


class Dog(Animal):
    def noise(self) -> str:
        return "woof"


@overload
def who(a: Animal) -> str: ...
@overload
def who(a: Dog) -> str: ...
def who(a: Animal | Dog) -> str:
    if isinstance(a, Dog):
        return "dog"
    return "other"


def via(a: Animal) -> str:
    return who(a)


# reference parameter: the copy mutates the caller's list
@overload
def push(xs: list[int32], v: int32) -> int32: ...
@overload
def push(xs: list[int32], v: str) -> int32: ...
def push(xs: list[int32], v: int32 | str) -> int32:
    if isinstance(v, str):
        xs.append(len(v))
    else:
        xs.append(v)
    return len(xs)


# @auto_readonly implementation
class Shelf:
    items: list[Tag]

    def __init__(self) -> None:
        self.items = [Tag("a"), Tag("b")]

    @overload
    def at(self, i: int32) -> Tag: ...
    @overload
    def at(self, i: str) -> Tag: ...
    @auto_readonly
    def at(self, i: int32 | str) -> Tag:
        if isinstance(i, str):
            return self.items[len(i) - 1]
        return self.items[i]


# @auto_readonly stubs over a mixed implementation return: each copy returns
# its own stub's type, which marks the part it returns
class Rows:
    data: list[Tag]

    def __init__(self) -> None:
        self.data = [Tag("r"), Tag("s")]

    @overload
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[Tag]: ...
    @overload
    @auto_readonly
    def __getitem__(self, index: slice) -> Span[auto_readonly[Tag]]: ...
    @auto_readonly
    def __getitem__(self, index: int32 | slice) -> Tag | Span[auto_readonly[Tag]]:  # tpyc: ok
        if isinstance(index, slice):
            return self.data[0:1]
        return self.data[index]


# @error_return implementation
class Bad(Exception, ReturnException):
    pass


@overload
def checked(a: int32) -> int32: ...
@overload
def checked(a: int32, b: int32) -> int32: ...
@error_return(Bad)
def checked(a: int32, b: int32 = 0) -> int32:
    if a + b < 0:
        raise Bad()
    return a + b


# sub-union stub: one stub keeps a union of two members
@overload
def sub(x: int32 | str) -> str: ...
@overload
def sub(x: float) -> str: ...
def sub(x: int32 | str | float) -> str:
    if isinstance(x, float):
        return "float"
    if isinstance(x, str):
        return "str"
    return "int"


# match body, a guard and a reference capture
class Cat:
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives


class Owner:
    pet: Cat

    def __init__(self, pet: Cat) -> None:
        # the constructor's own store, not the overload's
        self.pet = pet  # tpyc: warning(/copies Cat into field/)


@overload
def describe(a: Cat, k: int32) -> str: ...
@overload
def describe(a: Owner, k: int32) -> str: ...
def describe(a: Cat | Owner, k: int32) -> str:
    match a:
        case Cat(lives=n) if k > 0:
            return "cat " + str(n)
        case Owner(pet=p):
            p.lives += 1
            return "owner " + str(p.lives)
        case _:
            return "other"


# a dynamic test before the decided one
@overload
def judge(a: Cat, n: int32) -> str: ...
@overload
def judge(a: Owner, n: int32) -> str: ...
def judge(a: Cat | Owner, n: int32) -> str:
    if n > 3:
        return "big"
    elif isinstance(a, Cat):
        return "cat"
    else:
        return "owner"


# isinstance in a ternary and a while
@overload
def tern(x: int32) -> str: ...
@overload
def tern(x: str) -> str: ...
def tern(x: int32 | str) -> str:
    out = "s" if isinstance(x, str) else "i"
    while isinstance(x, str) and len(out) < 3:
        out += "!"
    return out


# a container stub narrows the implementation's parameter
@overload
def size(x: list[int64]) -> int32: ...
@overload
def size(x: str) -> int32: ...
def size(x: list[int64] | str) -> int32:
    return len(x)


# a structural-protocol stub mutates the list element it is handed
class Pinger(Protocol):
    def ping(self) -> int32: ...


class Bell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def ping(self) -> int32:
        self.n += 1
        return self.n


class Gong:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def ping(self) -> int32:
        self.n += 10
        return self.n


class Tower:
    bells: list[Bell]
    lead: Bell
    spare: Bell | None
    mixed: Bell | Gong

    def __init__(self) -> None:
        self.bells = [Bell(3)]
        self.lead = Bell(3)
        self.spare = Bell(3)
        self.mixed = Bell(3)


@overload
def ring(q: Pinger) -> int32: ...
@overload
def ring(q: Pinger, k: int32) -> int32: ...
def ring(q: Pinger, k: int32 = 0) -> int32:
    return q.ping() + k


# a decided condition still runs its effects
def tick(a: Cat) -> int32:
    a.lives += 1
    return a.lives


@overload
def effect(x: int32, a: Cat) -> int32: ...
@overload
def effect(x: str, a: Cat) -> int32: ...
def effect(x: int32 | str, a: Cat) -> int32:
    if tick(a) > 100 and isinstance(x, str):
        return -1
    return a.lives


# a match capture named like an existing local rebinds that local
@overload
def pre(a: Cat) -> int32: ...
@overload
def pre(a: Owner) -> int32: ...
def pre(a: Cat | Owner) -> int32:
    lives = 5
    match a:
        case Cat(lives=lives):  # tpyc: ok
            pass
        case _:
            pass
    return lives


# a decided test still runs a walrus operand and an `or`'s call
def log(msg: str) -> bool:
    print("log", msg)
    return False


@overload
def walrus(x: int32, k: int32) -> int32: ...
@overload
def walrus(x: str, k: int32) -> int32: ...
def walrus(x: int32 | str, k: int32) -> int32:
    m = 0
    if (m := k) > 100 and isinstance(x, str):  # tpyc: ok
        return -1
    if log("cond") or isinstance(x, str):  # tpyc: ok
        m += 1
    return m


# a generator method and a coroutine method over a union stub parameter; the
# coroutine narrows a value parameter, not the union one
# (BUGS.md#async-method-union-param-narrow-rejects)
class Feed:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    @overload
    def items(self, x: Cat | Owner) -> Iterator[int32]: ...
    @overload
    def items(self, x: str) -> Iterator[int32]: ...
    def items(self, x: Cat | Owner | str) -> Iterator[int32]:  # tpyc: ok
        if isinstance(x, Cat):
            x.lives += 1
            yield x.lives + self.base
        elif isinstance(x, Owner):
            yield x.pet.lives + self.base
        else:
            yield len(x) + self.base

    @overload
    async def fetch(self, x: Cat | Owner, k: int32) -> int32: ...
    @overload
    async def fetch(self, x: Cat | Owner, k: str) -> int32: ...
    async def fetch(self, x: Cat | Owner, k: int32 | str) -> int32:  # tpyc: ok
        n = weigh(x)
        if isinstance(k, str):
            return n + len(k)
        return n + k


def weigh(x: Cat | Owner) -> int32:
    if isinstance(x, Cat):
        x.lives += 2
        return x.lives
    return x.pet.lives


# coroutine: one frame per stub
@overload
async def co(x: int32) -> int32: ...
@overload
async def co(x: str) -> int32: ...
async def co(x: int32 | str) -> int32:
    if isinstance(x, str):
        return len(x)
    return x + 1


async def run_co() -> None:
    print("coroutine", await co(1), await co("xyz"))
    feed = Feed(100)
    cat = Cat(1)
    got = await feed.fetch(cat, 10)
    # the coroutine copy wrote the caller's Cat
    print("coroutine method", got, await feed.fetch(cat, "ab"), cat.lives)


# an omitted argument ranks as the implementation's default written there:
# a default out of a copy's range is no default of it, so `wide_cell()` runs
# the int64 copy as `wide_cell(3000000000)` does
@overload
def wide_cell(x: int32 = 0) -> str: ...
@overload
def wide_cell(x: int64 = 3000000000) -> str: ...
def wide_cell(x: int32 | int64 = 3000000000) -> str:  # tpyc: ok
    return "wide " + str(x)


# boundary: 127 fits the int8 copy, 128 only the int32 one
@overload
def edge8(x: int8 = 0) -> str: ...
@overload
def edge8(x: int32, y: str) -> str: ...
def edge8(x: int8 | int32 = 127, y: str = "s") -> str:  # tpyc: ok
    return "edge8 " + str(x) + y


@overload
def past8(x: int8 = 0) -> str: ...
@overload
def past8(x: int32 = 0) -> str: ...
def past8(x: int8 | int32 = 128) -> str:  # tpyc: ok
    return "past8 " + str(x)


# a written literal out of a Literal set's default width ranks by its value;
# without the int64 variant `lit_wide(3000000000)` is refused at the int32
# range check, as on a plain int32 parameter
@dispatch
def lit_wide(x: Literal[1]) -> str:
    return "lit one"


@dispatch
def lit_wide(x: int32) -> str:
    return "lit i32 " + str(x)


@dispatch
def lit_wide(x: int64) -> str:
    return "lit i64 " + str(x)


# the omitted cell ranks as the written `rank_cell(b, 5)` does: an int
# literal at its exact width, so the A copy runs `rank_cell(b)` as the
# int32-spelled default does (pinned in the C++)
class RankA:
    def __init__(self) -> None:
        pass


class RankB(RankA):
    pass


@overload
def rank_cell(x: RankA, y: int32 = 5) -> str: ...
@overload
def rank_cell(x: RankB) -> str: ...
def rank_cell(x: RankA | RankB, y: int32 = 5) -> str:  # tpyc: ok
    return "y" + str(y)


@overload
def rank_typed(x: RankA, y: int32 = int32(5)) -> str: ...
@overload
def rank_typed(x: RankB) -> str: ...
def rank_typed(x: RankA | RankB, y: int32 = int32(5)) -> str:  # tpyc: ok
    return "y" + str(y)


# a char constant as a char copy's default: `ch()` runs it as `ch(Q)` does
# (a "q" literal default is refused, BUGS.md#dispatch-str-literal-to-char-refused)
Q: Final[char] = "q"


@overload
def ch(x: char = Q) -> str: ...
@overload
def ch(x: char, y: int32) -> str: ...
def ch(x: char = Q, y: int32 = 0) -> str:  # tpyc: ok
    return "ch " + str(x) + str(y)


# a one-character str literal runs the str variant beside a char one
@dispatch
def chs(x: str) -> str:
    return "chs str " + x


@dispatch
def chs(x: char) -> str:
    return "chs char"


# an enum member, a class constant and a wider-int default, each ranked as
# the same expression written there
class Mode(Enum):
    R = 1
    W = 2


class Holder:
    K: Final[int32] = 9


@overload
def by_mode(m: Mode = Mode.R) -> str: ...
@overload
def by_mode(m: str) -> str: ...
def by_mode(m: Mode | str = Mode.R) -> str:  # tpyc: ok
    return "mode " + (m if isinstance(m, str) else "enum")


@overload
def by_const(k: int32 = 0) -> str: ...
@overload
def by_const(k: str) -> str: ...
def by_const(k: int32 | str = Holder.K) -> str:  # tpyc: ok
    return "const " + str(k)


@overload
def by_wide(x: int64 = 0) -> str: ...
@overload
def by_wide(x: str = "") -> str: ...
def by_wide(x: int64 | str = 0) -> str:  # tpyc: ok
    return "wide64 " + ("str" if isinstance(x, str) else str(x))


# method set: an omitted argument ranks as the implementation's default
# written there, as for a free function -- `w.m()` runs the int64 copy
class Wide:
    def __init__(self) -> None:
        pass

    @overload
    def m(self, x: int32 = 0) -> str: ...
    @overload
    def m(self, x: int64 = 3000000000) -> str: ...
    def m(self, x: int32 | int64 = 3000000000) -> str:  # tpyc: ok
        return "m " + str(x)

    @overload
    def e(self, x: int8 = 0) -> str: ...
    @overload
    def e(self, x: int32, y: str) -> str: ...
    def e(self, x: int8 | int32 = 127, y: str = "s") -> str:  # tpyc: ok
        return "e " + str(x) + y


# a one-character literal takes a method's and a constructor's char
# parameter as a free function's
class Chm:
    c: char

    def __init__(self, c: char) -> None:
        self.c = c

    def set(self, x: char) -> None:
        self.c = x


# a forwarded Literal["q"] parameter runs the generic variant, as the
# written "q" does
@dispatch
def fwd(x: char) -> str:
    return "fwd char"


@dispatch
def fwd[T](x: T) -> str:
    return "fwd generic"


def forward(s: Literal["q"]) -> str:
    return fwd(s)  # tpyc: ok


# stubs told apart by a callback: a str literal ranks at their Literal
# parameter, written or omitted
@overload
def cb(f: Callable[[int32], int32], m: Literal["a", "b"] = "a") -> str: ...
@overload
def cb(f: Callable[[int64], int64], m: Literal["a", "b"] = "a") -> str: ...
def cb(f: Callable[[int32], int32] | Callable[[int64], int64], m: Literal["a", "b"] = "a") -> str:  # tpyc: ok
    return "cb " + str(f(2)) + m


def inc(n: int32) -> int32:
    return n + 1


def inc64(n: int64) -> int64:
    return n + 2


def main() -> None:
    print("free", area(Square(2.0)), area(Circle(1.0)))
    shop = Shop()
    print("method", shop.sell(Square(1.0)), shop.sell(Circle(1.0)), shop.sold)
    print("static", Shop.kind(Square(1.0)), Shop.kind(Circle(1.0)))
    print("generic", first([4, 5]), first(["y", "z"], 1))
    print("generic class", Box[str]("v").get(1), Box[int32](3).get("a"))
    print("generator", [i for i in count(3)], [i for i in count(6, 2)])
    asyncio.run(run_co())
    print("short scalar", open_mode(), open_mode("w"))
    print("short none", fmt(1), fmt(2, "t"))
    t = Tag("n")
    print("short ref", label(1), label(2, t))
    t.name = "m"  # the copy reads the caller's Tag, not a copy of it
    print("short ref after", label(3, t), t.name)
    print("defaults", k(), k(3), dflt(1), dflt("ab"))
    print("kwonly", kw(1), kw("a"), kw(1, y=2))
    print("filled other", wide(), wide("t"))
    print("short omitted", short(1), short("a"), short("a", 2))
    print("generic posonly", gpo(1), gpo(1, 2), gpo(1, 2, "s"))
    print("filled", st(), st("rb"))
    print("filled both", pick(), pick("r"), pick("x"))
    print("filled kwonly", kwfill(1), kwfill(2, mode="rb"), kwfill(3, mode="w"))
    print("filled posonly", po(), po(2), po(3, "w"), pokw(x=2), pokw("w"))
    op = Opener()
    print("filled method", op.st(), op.st("rb"), op.opened)
    print("literal union", triple(3), triple("ab"))
    ft = flag(True)
    ff = flag(False)
    print("bool", ft, ff)
    plain_flag(True)
    print("short self", Counter(3).total(), Counter(3).total(Counter(4)))
    print("default shadow", shade(1), shade(1, 2), plain_shade(1), plain_shade(1, 2))
    print("literal width", power(2) == 2 ** 70, power(3) == 3 ** 70)
    d = Dog()
    print("poly", via(d), who(d))
    xs = [1]
    n = push(xs, 2)
    n = push(xs, "abc")
    print("list", n, xs)  # the copy appended to the caller's list
    shelf = Shelf()
    got = shelf.at(0)
    got.name = "z"  # the mutable twin hands out the shelf's own Tag
    print("auto_readonly", shelf.at("a").name, shelf.at(1).name)
    rows = Rows()
    row = rows[1]
    row.name = "q"  # the mutable copy hands out the row itself
    print("auto_readonly set", rows[1].name, len(rows[0:2]), rows.data[1].name)
    try:
        print("error_return", checked(1), checked(1, 2))
        checked(-5)
    except Bad:
        print("error_return raised")
    print("sub-union", sub(1), sub("a"), sub(1.5))
    c = Cat(3)
    o = Owner(Cat(3))
    # the reference capture writes the owner's own Cat
    print("match", describe(c, 1), describe(c, 0), describe(o, 0), o.pet.lives)
    print("dynamic first", judge(c, 1), judge(o, 1), judge(c, 5))
    print("ternary while", tern(1), tern("a"))
    ms: list[int64] = [1, 2]
    print("container", size(ms), size("abc"))
    tower = Tower()
    print("protocol element", ring(tower.bells[0]), tower.bells[0].n, ring(tower.bells[0], 10))
    # a field, an Optional field and a narrowed union field: each ring wrote the tower's own bell
    print("protocol field", ring(tower.lead), tower.lead.n)  # tpyc: ok
    if tower.spare is not None:
        print("protocol optional", ring(tower.spare), tower.spare.n)  # tpyc: ok
    mixed = tower.mixed
    if isinstance(mixed, Bell):
        print("protocol union", ring(mixed), mixed.n)  # tpyc: ok
    print("effects", effect(1, Cat(5)), effect("a", Cat(5)))
    print("capture local", pre(Cat(1)), pre(o))
    wi = walrus(1, 7)
    ws = walrus("a", 7)
    print("walrus", wi, ws)
    feed = Feed(10)
    kitty = Cat(1)
    # the generator copy wrote the caller's Cat
    print("generator method", [v for v in feed.items(kitty)], [v for v in feed.items(o)], [v for v in feed.items("abc")], kitty.lives)
    print("module generic", ovlib.ident(7, 3), ovlib.ident("a"))
    print("short keyword", kshort(1), kshort(x=1), kshort(x=1, y="ab"))  # tpyc: ok
    bag = Bag()
    print("implicit dunder", 1 in bag, bag.__contains__(1), bag.__contains__(1, "w"))  # tpyc: ok
    print("named drop", named(1), named("a", "b"))  # tpyc: ok
    print("module generator", [x for x in ovlib.rep(1, 2)], [x for x in itertools.repeat(4, 2)])
    print("omitted wide", wide_cell(), wide_cell(3000000000), wide_cell(1))  # tpyc: ok
    print("omitted edge", edge8(), past8())  # tpyc: ok
    print("literal wide", lit_wide(3000000000), lit_wide(1), lit_wide(7))  # tpyc: ok
    rb = RankB()
    print("omitted rank", rank_cell(rb), rank_cell(rb, 5), rank_typed(rb))  # tpyc: ok
    print("char", ch(), ch(Q), ch(Q, 1), chs("a"), chs("ab"))  # tpyc: ok
    print("member defaults", by_mode(), by_mode("x"), by_const(), by_const("y"), by_wide(), by_wide("z"))  # tpyc: ok
    w = Wide()
    print("omitted method", w.m(), w.m(3000000000), w.m(1), w.e())  # tpyc: ok
    p = Chm("a")  # tpyc: ok
    print("char method", p.c)
    p.set("q")  # tpyc: ok
    print("char method set", p.c)
    print("char forward", forward("q"), fwd("q"))  # tpyc: ok
    print("literal callback", cb(inc), cb(inc, "b"), cb(inc64, "b"))  # tpyc: ok

main()
