# A ValueType record binds, passes and returns like a scalar; every slot
# declared before its first value takes the type's default constructor, and
# `P()` runs `__init__` once. The slot sections use side-effect-free
# `__init__`s: a placeholder runs one callable without arguments an extra time
# (LANGUAGE_FEATURES "Placeholders run the default constructor";
# records/value_type_placeholder_default_ctor pins that run).
import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final, Iterator, Protocol
from tpy import int32, Array, Own, ValueType, copy, error_return, ReturnException, make_default
import other
from other import Far

K: Final[int32] = 7


class Coord(ValueType):
    column: int32
    row: int32

    def __init__(self, column: int32, row: int32) -> None:
        self.column = column
        self.row = row

    def swapped(self) -> "Coord":
        return Coord(self.row, self.column)


class Pair[T: ValueType](ValueType):
    first: T
    second: T

    def __init__(self, first: T, second: T) -> None:
        self.first = first
        self.second = second


class Segment(ValueType):
    a: Coord
    b: Coord

    def __init__(self, a: Coord, b: Coord) -> None:
        self.a = a
        self.b = b


# A union field keeps the default constructor: the variant builds its first
# alternative.
class Tagged(ValueType):
    value: int32 | str
    n: int32

    def __init__(self, value: int32 | str, n: int32) -> None:
        self.value = value
        self.n = n


# An all-defaulted `__init__` that prints, built only by calls: it runs once
# per call.
class Noisy(ValueType):
    n: int32

    def __init__(self, n: int32 = K) -> None:
        print("noisy init", n)
        self.n = n

    def __eq__(self, other: "Noisy") -> bool:
        return self.n == other.n

    @classmethod
    def fresh(cls) -> "Noisy":
        return cls()  # tpyc: ok


class NoisyChild(Noisy):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


class NoisyHeir(Noisy):
    pass


class NoisyValueHeir(Noisy, ValueType):
    pass


# An all-defaulted `__init__` without side effects, the type of every slot
# section: it is the C++ default constructor a placeholder runs.
class Calm(ValueType):
    n: int32

    def __init__(self, n: int32 = K) -> None:
        self.n = n

    def __eq__(self, other: "Calm") -> bool:
        return self.n == other.n


# A generic ValueType whose `__init__` defaults every argument.
class Cell[T](ValueType):
    k: int32

    def __init__(self, k: int32 = 5) -> None:
        print("cell init", k)
        self.k = k


class IntCell(Cell[int32]):
    pass


# A generic ValueType whose side-effect-free `__init__` defaults every
# argument.
class Gauge[T](ValueType):
    k: int32

    def __init__(self, k: int32 = 5) -> None:
        self.k = k


# A factory field: the generated `__init__` defaults every argument, so it is
# the default constructor.
@dataclass
class Outer(ValueType):
    z: Calm = field(default_factory=Calm)
    k: int32 = 1


# A parameterless `__init__` without side effects.
class NoArgs(ValueType):
    n: int32
    s: str

    def __init__(self) -> None:
        self.n = 7
        self.s = "hi"


# A subclass adding a field: `RH()` runs the inherited `__init__`.
class RH(Noisy):
    m: int32 = 5


# A ValueType subclass adding a field, with its own defaulted `__init__`.
class DVH(Noisy, ValueType):
    m: int32

    def __init__(self, n: int32 = 8) -> None:
        super().__init__(n)
        self.m = 5


# A ValueType subclass with a required parameter: its placeholder builds the
# base with the base's default constructor.
class RVH(Calm, ValueType):
    m: int32

    def __init__(self, n: int32) -> None:
        super().__init__(n)
        self.m = 5


# A keyword-only required parameter after a defaulted first one.
class KW(ValueType):
    a: int32
    b: int32

    def __init__(self, a: int32 = 1, *, b: int32) -> None:
        print("kw init", a, b)
        self.a = a
        self.b = b


class Grid:
    cells: Array[Noisy, 2]

    def __init__(self) -> None:
        # field: each element runs `__init__`
        self.cells = Array[Noisy, 2]()  # tpyc: ok


class Sized(Protocol):
    def size(self) -> int32: ...


# The first defaulted parameter is a structural protocol (a template
# parameter in C++).
class Measured(ValueType):
    n: int32 = 0

    def __init__(self, s: Sized | None = None, k: int32 = 1) -> None:
        print("measured init", k)
        if s is not None:
            self.n = s.size() + k
        else:
            self.n = k


@dataclass
class Settings(ValueType):
    a: int32 = 3
    b: int32 = 4

    def __post_init__(self) -> None:
        print("settings init", self.a, self.b)


@dataclass
class Crate:
    z: Noisy = field(default_factory=Noisy)


# A str field makes a use-after-destroy of a rebound slot observable.
class Text(ValueType):
    s: str

    def __init__(self, s: str = "abcdefghijklmnopqrstuvwxyz0123456789") -> None:
        self.s = s


# The union field builds its first alternative, so a subclass may skip
# `super().__init__` over an `__init__`-less base.
class TagBase:
    tag: int32 | str


class TagChild(TagBase):
    k: int32

    # skips super(): reads only its own field
    def __init__(self, k: int32) -> None:  # tpyc: ok
        self.k = k


# A non-generic ValueType whose field's `__init__` defaults every argument.
class Wrapped(ValueType):
    z: Calm
    k: int32

    def __init__(self, z: Calm, k: int32) -> None:
        self.z = z
        self.k = k


type CalmTree = Calm | list[CalmTree]


class Holder:
    c: Coord
    maybe: Coord | None

    def __init__(self, c: Coord) -> None:
        self.c = c
        # BUGS.md#valuetype-optional-ctor-member-init keeps the value out
        # of the member-init list.
        self.maybe = None

    # method: return of the whole optional field
    def get_maybe(self) -> Coord | None:
        return self.maybe  # tpyc: ok


class Frame:
    visual: Coord

    def __init__(self, visual: Coord) -> None:
        self.visual = visual


class Walker:
    pos: Coord
    frames: list[Frame]

    def __init__(self) -> None:
        self.pos = Coord(1, 1)
        self.frames = [Frame(Coord(3, 4)), Frame(Coord(5, 6))]

    # method: return of a rebound local
    def stepped(self, d: int32) -> Coord:
        c = self.pos
        c = Coord(c.column + d, c.row)
        return c  # tpyc: ok

    # method: return of a plain field
    def at(self) -> Coord:
        return self.pos  # tpyc: ok

    # method: return of an element's field
    def visual(self, i: int32) -> Coord:
        return self.frames[i].visual  # tpyc: ok


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, kind, value, tb) -> None:
        pass


class Bad(Exception, ReturnException):
    pass


def use(c: Coord) -> int32:
    return c.column * 10 + c.row


def make(n: int32) -> Coord:
    return Coord(n, n + 1)


@error_return(Bad)
def checked(c: Coord) -> int32:
    if c.column < 0:
        raise Bad()
    return use(c)


# free function: return of a rebound local
def ret_free(n: int32) -> Coord:
    c = Coord(0, 0)
    c = make(n)
    return c  # tpyc: ok


# try/finally: return of a rebound local
def ret_try_finally(n: int32) -> Coord:
    c = Coord(1, 2)
    try:
        c = Coord(n, n)
        return c  # tpyc: ok
    finally:
        print("ret try_finally: finally")


# with body: return of a rebound local
def ret_with(n: int32) -> Coord:
    with Ctx():
        c = Coord(1, 2)
        c = Coord(n, c.row)
        return c  # tpyc: ok


# generic record: return of a rebound local
def swap_pair(p: Pair[int32]) -> Pair[int32]:
    q = p
    q = Pair(q.second, q.first)
    return q  # tpyc: ok


# builtin-module record: return of a rebound local
def next_day(d: date) -> date:
    d = d + timedelta(days=1)
    return d  # tpyc: ok


# match arm: return of a rebound local
def ret_match(n: int32) -> Coord:
    c = Coord(0, 0)
    match n:
        case 1:
            c = Coord(1, 1)
        case _:
            c = Coord(9, 9)
    return c  # tpyc: ok


def returns() -> None:
    print("ret free", use(ret_free(3)))
    w = Walker()
    # Bound first: BUGS.md#valuetype-arg-shape-keyed-rows.
    stepped = w.stepped(4)
    print("ret method", use(stepped))
    at = w.at()
    vis = w.visual(1)
    print("ret field", use(at), use(vis))
    swapped = swap_pair(Pair(1, 2))
    print("ret generic", swapped.first, swapped.second)
    print("ret date", next_day(date(2024, 2, 28)).day)
    # Bound first: BUGS.md#print-arg-output-interleaves.
    settled = ret_try_finally(5)
    print("ret try_finally", use(settled))
    print("ret with", use(ret_with(6)))
    print("ret match", use(ret_match(1)), use(ret_match(2)))
    base = Coord(1, 2)

    # closure: return of a rebound local
    def shift(d: int32) -> Coord:
        c = base
        c = Coord(c.column + d, c.row)
        return c  # tpyc: ok

    print("ret closure", use(shift(3)))


def field_reads() -> None:
    h = Holder(Coord(1, 2))
    h.maybe = Coord(3, 4)
    # plain field into a local
    c = h.c  # tpyc: ok
    # Optional field into a local
    m = h.maybe  # tpyc: ok
    print("field plain", use(c))
    if m is not None:
        # Read through fields: BUGS.md#valuetype-arg-shape-keyed-rows.
        print("field optional", m.column, m.row)
    s = Segment(Coord(1, 2), Coord(3, 4))
    a = s.a
    # nested ValueType field as a constructor argument
    s = Segment(s.b, a)  # tpyc: ok
    print("field nested", use(s.a), use(s.b))


def arguments() -> None:
    a = Coord(1, 2)
    # constructor temporary
    print("arg ctor", use(Coord(3, 4)))  # tpyc: ok
    # call result
    print("arg call", use(make(5)))  # tpyc: ok
    # keyword argument
    print("arg kwarg", use(c=Coord(6, 7)))  # tpyc: ok
    # explicit copy
    print("arg copy", use(copy(a)))  # tpyc: ok
    try:
        # an @error_return callee taking the Coord (one returning it hits
        # BUGS.md#valuetype-arg-shape-keyed-rows at the argument)
        print("arg error_return", checked(Coord(8, 9)))  # tpyc: ok
    except Bad:
        print("arg error_return: bad")


# generator: a frame local and a Coord yield
def walk(n: int32) -> Iterator[Coord]:
    c = Coord(0, 0)  # tpyc: ok
    for i in range(n):
        c = Coord(c.column + 1, i)
        yield c  # tpyc: ok


# generator: a generic-record frame local
def pair_seconds(n: int32) -> Iterator[int32]:
    q = Pair(0, n)  # tpyc: ok
    yield q.second
    q = Pair(q.second, q.first)
    yield q.second


# generator: a builtin-module record frame local
def days(d: date, n: int32) -> Iterator[int32]:
    for _ in range(n):
        d = d + timedelta(days=1)  # tpyc: ok
        yield d.day


# generator: a loop variable over a list of Coords
def columns(xs: list[Coord]) -> Iterator[int32]:
    for c in xs:  # tpyc: ok
        yield c.column


# generator: a frame local rebound from a method result
def flips(c: Coord) -> Iterator[Coord]:
    yield c
    c = c.swapped()  # tpyc: ok
    yield c


# generator: a frame local bound from a `Coord | None` call, read after a
# suspension
def maybe_columns(n: int32) -> Iterator[int32]:
    c = maybe_at(n)  # tpyc: ok
    yield 0
    if c is not None:
        yield c.column


# async: a frame local returned after a suspension
async def settle(n: int32) -> Coord:
    c = Coord(1, 2)  # tpyc: ok
    await asyncio.sleep(0)
    if n > 0:
        c = Coord(n, n)
    return c  # tpyc: ok


# async: a frame local rebound from a method result
async def flip_later(n: int32) -> Coord:
    c = Coord(n, n + 1)
    await asyncio.sleep(0)
    c = c.swapped()  # tpyc: ok
    return c


async def resumables() -> None:
    for c in walk(3):
        print("generator", use(c))
    print("generator generic", [v for v in pair_seconds(5)])
    print("generator date", [v for v in days(date(2024, 2, 27), 3)])
    print("generator loop var", [v for v in columns([Coord(1, 2), Coord(3, 4)])])
    print("generator method result", [use(c) for c in flips(Coord(1, 2))])
    print("generator optional call", [v for v in maybe_columns(4)],
          [v for v in maybe_columns(0)])
    print("async", use(await settle(4)))
    print("async method result", use(await flip_later(3)))


# `Coord | None` return of a local
def maybe_at(n: int32) -> Coord | None:
    c: Coord | None = None
    if n > 0:
        c = Coord(n, n)
    return c  # tpyc: ok


# `Coord | None` return of a plain Coord local
def plain_at(n: int32) -> Coord | None:
    c = Coord(n, n + 1)
    if n > 0:
        return c  # tpyc: ok
    return None


# `Coord | None` return of a narrowed local
def narrowed_at(n: int32) -> Coord | None:
    c = maybe_at(n)
    if c is not None:
        return c  # tpyc: ok
    return None


def optionals() -> None:
    for n in [0, 2]:
        r = maybe_at(n)
        if r is not None:
            # Read through fields: BUGS.md#valuetype-arg-shape-keyed-rows.
            print("optional", r.column, r.row)
        else:
            print("optional none")
        p = plain_at(n)
        if p is not None:
            print("optional plain", p.column, p.row)
        else:
            print("optional plain none")
        q = narrowed_at(n)
        if q is not None:
            print("optional narrowed", q.column, q.row)
        else:
            print("optional narrowed none")
    h = Holder(Coord(1, 2))
    h.maybe = Coord(7, 8)
    f = h.get_maybe()
    if f is not None:
        print("optional field", f.column, f.row)


# tuple element return
def paired(c: Coord) -> tuple[Coord, int32]:
    return (c, c.row)  # tpyc: ok


def tuples() -> None:
    t = paired(Coord(1, 2))
    # unpack
    c, n = t  # tpyc: ok
    print("tuple unpack", use(c), n)
    # literal rebind
    u = (Coord(3, 4), 1)
    u = (Coord(5, 6), 2)  # tpyc: ok
    print("tuple rebind", use(u[0]), u[1])


def dict_items() -> None:
    d = {"a": Coord(1, 2), "b": Coord(3, 4)}
    total = 0
    # for-loop: a Coord unpacked from the dict items
    for k, v in d.items():  # tpyc: ok
        total += use(v)
    print("dict items", total)


def walrus() -> None:
    # walrus in a condition: the Coord is pre-declared
    if (c := make(3)).row > 1:  # tpyc: ok
        print("walrus", use(c))


G = Coord(1, 2)

# module level: globals of an all-defaulted ValueType
ORIGIN = Calm()  # tpyc: ok
FAR: Calm = Calm(9)  # tpyc: ok
near, near_count = Calm(3), 1  # tpyc: ok
# a module-level tuple holding Calm (read at module level: a function read
# rejects, BUGS.md#record-tuple-global-element-read-rejects)
near_pair = (Calm(4), 4)  # tpyc: ok
print("module tuple", near_pair[0].n, near_pair[1])


def set_global() -> None:
    global G
    # a module-level global rebound from a function
    G = Coord(5, 6)  # tpyc: ok


def ternary(f: bool) -> None:
    a = Coord(1, 2)
    b = Coord(3, 4)
    # two constructor arms
    c = Coord(5, 6) if f else Coord(7, 8)  # tpyc: ok
    # two name arms
    d = a if f else b  # tpyc: ok
    # a name arm beside a constructor arm
    e = a if f else Coord(9, 9)  # tpyc: ok
    print("ternary", f, use(c), use(d), use(e))


def union_field_slots(n: int32) -> None:
    # if hoist
    if n > 0:
        t = Tagged(n, 1)  # tpyc: ok
    else:
        t = Tagged(-n, 2)
    print("union if", t.n)
    # try hoist
    try:
        u = Tagged(n, 3)  # tpyc: ok
    except ValueError:
        return
    print("union try", u.n)
    # with body
    with Ctx():
        w = Tagged(n, 4)  # tpyc: ok
    print("union with", w.n)
    # for loop variable read after the loop
    for x in [Tagged(1, 5), Tagged(6, 6)]:  # tpyc: ok
        pass
    print("union for after", x.n)
    # match hoist
    match n:
        case 1:
            m = Tagged(n, 7)  # tpyc: ok
        case _:
            m = Tagged(8, 8)
    print("union match", m.n)
    # walrus
    if (k := Tagged(n, 9)).n > 0:  # tpyc: ok
        print("union walrus", k.n)


# generator: a union-field frame local and loop variable
def tagged_gen(n: int32) -> Iterator[int32]:
    t = Tagged(n, 1)  # tpyc: ok
    yield t.n
    for x in [Tagged(2, 2), Tagged(3, 3)]:  # tpyc: ok
        yield x.n
    yield t.n


# generator: a generic record of it as a frame local
def tagged_pairs(n: int32) -> Iterator[int32]:
    q = Pair(Tagged(n, 4), Tagged(5, 5))  # tpyc: ok
    yield n
    yield q.second.n


# async: a union-field frame local
async def tagged_async(n: int32) -> int32:
    t = Tagged(n, 6)  # tpyc: ok
    await asyncio.sleep(0)
    return t.n


def calm_try() -> None:
    try:
        # try hoist
        z = Calm()  # tpyc: ok
    except ValueError:
        return
    print("calm try", z.n)


def calm_slots(n: int32) -> None:
    # annotation-only decl
    z: Calm  # tpyc: ok
    z = Calm(n)
    print("calm annotated", z.n)
    # walrus
    if (w := Calm(n + 1)).n > 0:  # tpyc: ok
        print("calm walrus", w.n)
    # match hoist
    match n:
        case 1:
            m = Calm(10)  # tpyc: ok
        case _:
            m = Calm(11)
    print("calm match", m.n)


def calm_more_slots(f: bool) -> None:
    # with body
    with Ctx():
        w = Calm(12)  # tpyc: ok
    print("calm with", w.n)
    # for loop variable read after the loop
    for x in [Calm(13), Calm(14)]:  # tpyc: ok
        pass
    print("calm for after", x.n)
    # if hoist of a non-generic record holding a Calm
    if f:
        r = Wrapped(Calm(15), 1)  # tpyc: ok
    else:
        r = Wrapped(Calm(16), 2)
    print("calm wrapped", r.z.n, r.k)


@error_return(Bad)
def checked_calm(n: int32) -> Calm:
    if n < 0:
        raise Bad()
    return Calm(n)


@error_return(Bad)
def calm_bind(n: int32) -> int32:
    # @error_return: the unwrapped bind's slot
    z = checked_calm(n)  # tpyc: ok
    return z.n


@error_return(Bad)
def checked_tree(n: int32) -> Own[CalmTree]:
    if n < 0:
        raise Bad()
    return [Calm(n)]


@error_return(Bad)
def tree_bind(n: int32) -> int32:
    # recursive-alias wrapper: the unwrapped bind's slot
    t = checked_tree(n)  # tpyc: ok
    if isinstance(t, list):
        return 1
    return 0


# async: a frame local
async def calm_async(n: int32) -> int32:
    z = Calm(n)  # tpyc: ok
    await asyncio.sleep(0)
    return z.n


# async try/finally: the pending return's slot
async def calm_finally(n: int32) -> Calm:
    try:
        await asyncio.sleep(0)
        return Calm(n)  # tpyc: ok
    finally:
        await asyncio.sleep(0)
        print("calm finally body")


# generator: a frame field
def calm_gen(n: int32) -> Iterator[int32]:
    z = Calm(n)  # tpyc: ok
    yield 0
    yield z.n


# if hoist: return, copy, f-string and loop-body reads of the slot
def calm_hoist(f: bool) -> Calm:
    if f:
        p = Calm(1)
    else:
        p = Calm(2)
    w = p  # tpyc: ok
    print(f"calm hoist {p.n} {w.n}")  # tpyc: ok
    for _ in range(2):
        print("calm loop", p.n)  # tpyc: ok
    return p  # tpyc: ok


# if hoist of an element read: the slot holds a copy
def calm_subscript(xs: list[Calm], f: bool) -> None:
    if f:
        m = xs[0]  # tpyc: ok
    else:
        m = xs[1]
    xs[0] = Calm(5)
    print("calm subscript", m.n)


def pick_calm(f: bool) -> Calm | int32:
    if f:
        return Calm(1)
    return 2


# if hoist of a value union whose first alternative is Calm
def calm_union(f: bool) -> None:
    if f:
        v = pick_calm(True)  # tpyc: ok
    else:
        v = pick_calm(False)
    if isinstance(v, Calm):
        print("calm union", v.n)
    else:
        print("calm union", v)


# if hoist of a tuple holding Calm
def calm_tuple(f: bool) -> None:
    if f:
        t = (Calm(1), 1)  # tpyc: ok
    else:
        t = (Calm(2), 2)
    print("calm tuple", t[0].n, t[1])


# generator: a tuple frame local holding a ValueType (bound through a
# local: BUGS.md#tuple-literal-value-union-arg-rejects)
def tuple_gen(n: int32) -> Iterator[int32]:
    tg = Tagged(n, n)
    t = (tg, n)  # tpyc: ok
    # a tuple frame local of an all-defaulted record
    u = (Calm(n), n)  # tpyc: ok
    yield n
    yield t[0].n + t[1]
    yield u[0].n + u[1]


# zero-argument calls of an all-defaulted `__init__`: each runs it once, with
# its defaults
def spellings() -> None:
    print("spelling classmethod")
    # `cls()` in a classmethod
    a = Noisy.fresh()  # tpyc: ok
    print("spelling super")
    # `super().__init__()` without arguments
    b = NoisyChild()  # tpyc: ok
    print("spelling inherited")
    # inherited `__init__`
    c = NoisyHeir()  # tpyc: ok
    print("spelling dataclass")
    # dataclass-generated `__init__`
    d = Settings()  # tpyc: ok
    print("spelling by type")
    # a construction known only by type
    e = make_default[Noisy]()  # tpyc: ok
    print("spelling factory")
    # a default_factory
    f = Crate()  # tpyc: ok
    print("spelling protocol by type")
    # known only by type, a protocol first parameter
    g = make_default[Measured]()  # tpyc: ok
    print("spelling protocol keyword")
    # a keyword skipping the first parameter fills it at the call
    h = Measured(k=5)  # tpyc: ok
    print("spelling protocol")
    # a protocol first parameter
    i = Measured()  # tpyc: ok
    print("spellings", a.n, b.n, c.n, d.a, d.b, e.n, f.z.n, g.n, h.n, i.n)


# zero-argument construction resolves the first default in the type's module
def default_scope() -> None:
    K = 99
    # a local `K` does not shadow the constant the default names
    z = Noisy()  # tpyc: ok
    # an imported type's default names its own module's constant
    far = Far()  # tpyc: ok
    # module-qualified
    near_far = other.Far()  # tpyc: ok
    print("default scope", K, z.n, far.n, near_far.n)


def arrays() -> None:
    # local: each element runs `__init__`
    a = Array[Noisy, 3]()  # tpyc: ok
    print("array local", a[2].n)
    g = Grid()
    print("array field", g.cells[1].n)
    # nested
    c = Array[Array[Noisy, 2], 2]()  # tpyc: ok
    print("array nested", c[1][1].n)
    # an element type whose `T()` runs no code keeps the zero fill
    z = Array[int32, 3]()  # tpyc: ok
    print("array zero", z[0], z[2])


def inherited() -> None:
    # known only by type: the inherited `__init__` runs
    a = make_default[NoisyHeir]()  # tpyc: ok
    # a ValueType subclass
    b = NoisyValueHeir()  # tpyc: ok
    c = make_default[NoisyValueHeir]()  # tpyc: ok
    print("inherited", a.n, b.n, c.n)


def generic_cells() -> None:
    # generic ValueType
    a = Cell[int32]()  # tpyc: ok
    b = make_default[Cell[int32]]()  # tpyc: ok
    # a subclass of an instantiation
    c = IntCell()  # tpyc: ok
    d = make_default[IntCell]()  # tpyc: ok
    print("generic", a.k, b.k, c.k, d.k)


def factory_fields(f: bool) -> None:
    # built: the factory fills the field
    o = Outer()  # tpyc: ok
    # if hoist
    if f:
        p = Outer(Calm(1), 2)  # tpyc: ok
    else:
        p = Outer(Calm(3), 4)
    print("factory", o.z.n, o.k, p.z.n, p.k)


def no_args(f: bool) -> None:
    # a parameterless `__init__`, through a call
    a = NoArgs()  # tpyc: ok
    # ... and into a pre-declared slot
    if f:
        b = NoArgs()  # tpyc: ok
    else:
        b = NoArgs()
    print("no-args", a.n, a.s, b.n, b.s)


def heirs(f: bool) -> None:
    # a subclass adding a field: built directly and as array elements, each
    # running the inherited `__init__` once (`make_default[RH]()`:
    # BUGS.md#aggregate-brace-init-explicit-default-ctor)
    a = RH()  # tpyc: ok
    d = Array[RH, 2]()  # tpyc: ok
    print("heir", a.n, a.m, d[1].n, d[1].m)
    # a ValueType one built by type, and as array elements
    b = make_default[DVH]()  # tpyc: ok
    c = Array[DVH, 2]()  # tpyc: ok
    print("heir by type", b.n, b.m, c[1].n, c[1].m)
    # a ValueType one into a pre-declared slot
    if f:
        e = RVH(3)  # tpyc: ok
    else:
        e = RVH(4)
    print("heir value", e.n, e.m)


def keyword_only() -> None:
    # a required keyword-only parameter after a defaulted first one
    k = KW(b=2)  # tpyc: ok
    print("keyword only", k.a, k.b)


def generic_slots(f: bool) -> None:
    # if hoist of a generic ValueType whose `__init__` defaults everything
    if f:
        c = Gauge[int32](1)  # tpyc: ok
    else:
        c = Gauge[int32](2)
    # if hoist of a generic record over an all-defaulted ValueType
    a = Calm(3)
    b = Calm(4)
    if f:
        p = Pair(a, b)  # tpyc: ok
    else:
        p = Pair(b, a)
    print("generic slots", c.k, p.first.n, p.second.n)


# generator: a generic ValueType frame local
def gauge_gen(n: int32) -> Iterator[int32]:
    c = Gauge[int32](n)  # tpyc: ok
    yield 0
    yield c.k


def composite_slots(n: int32) -> None:
    # annotation-only decl of a tuple holding Calm
    t: tuple[Calm, int32]  # tpyc: ok
    t = (Calm(n), n)
    print("composite annotated", t[0].n, t[1])


def calm_copy(f: bool) -> None:
    a = Calm(1)
    if f:
        b = Calm(2)
    else:
        b = Calm(3)
    # a name rebinding the hoisted slot copies it
    b = a  # tpyc: ok
    print("calm copy", a.n, b.n)


# generator: a frame local rebound from itself
def text_rebinds(f: bool) -> Iterator[str]:
    z = Text()
    w = Text("w")
    yield z.s
    z = z  # tpyc: ok
    yield z.s
    z = z if f else w  # tpyc: ok
    yield z.s


class Label:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


# generator: a `frame_slot` rebound from itself (a union storing a fresh
# record no other name aliases, since the slot copies:
# BUGS.md#resumable-union-local-copies-reference; the long string makes
# reading a destroyed payload observable)
def union_rebinds() -> Iterator[str]:
    u: Label | int32 = 3
    yield str(u)
    u = Label("abcdefghijklmnopqrstuvwxyz0123456789")
    u = u  # tpyc: ok
    if isinstance(u, Label):
        yield u.s


# generator: a frame local stored into fields after a suspension
def store_gen(h: Holder) -> Iterator[int32]:
    c = Coord(5, 6)
    yield 0
    h.c = c  # tpyc: ok
    h.maybe = c  # tpyc: ok
    yield 1


# async: a frame local stored into fields after a suspension
async def store_async(h: Holder) -> None:
    c = Coord(7, 8)
    await asyncio.sleep(0)
    h.maybe = c  # tpyc: ok
    h.c = c  # tpyc: ok


def stores() -> None:
    h = Holder(Coord(1, 2))
    print("store generator", [v for v in store_gen(h)], use(h.c))
    m = h.maybe
    if m is not None:
        print("store generator optional", m.column, m.row)
    asyncio.run(store_async(h))
    n = h.maybe
    if n is not None:
        print("store async", use(h.c), n.column, n.row)


async def deferred_resumables() -> None:
    print("union generator", [v for v in tagged_gen(1)])
    print("union generator generic", [v for v in tagged_pairs(3)])
    print("union async", await tagged_async(2))
    # Bound first: BUGS.md#print-arg-output-interleaves.
    calm = [v for v in calm_gen(4)]
    print("calm generator", calm)
    print("calm async", await calm_async(20))
    finished = await calm_finally(21)
    print("calm finally", finished.n)
    print("text rebind", [v for v in text_rebinds(True)],
          [v for v in text_rebinds(False)])
    print("union rebind", [v for v in union_rebinds()])


def main() -> None:
    print("module", ORIGIN.n, FAR.n, near.n, near_count)
    r = calm_hoist(True)
    print("calm hoist return", r.n)
    calm_subscript([Calm(1), Calm(2)], True)
    calm_union(True)
    calm_union(False)
    calm_tuple(False)
    # Bound first: BUGS.md#print-arg-output-interleaves.
    tuples_seen = [v for v in tuple_gen(3)]
    print("tuple generator", tuples_seen)
    spellings()
    default_scope()
    arrays()
    inherited()
    generic_cells()
    factory_fields(True)
    no_args(False)
    heirs(True)
    keyword_only()
    generic_slots(False)
    # Bound first: BUGS.md#print-arg-output-interleaves.
    cells = [v for v in gauge_gen(6)]
    print("generic generator", cells)
    composite_slots(8)
    union_field_slots(1)
    print("skipped super", TagChild(3).k)
    calm_try()
    calm_slots(1)
    calm_more_slots(True)
    try:
        bound = calm_bind(17)
        print("calm error_return", bound)
        tree = tree_bind(18)
        print("calm tree", tree)
    except Bad:
        print("calm error_return: bad")
    calm_copy(True)
    asyncio.run(deferred_resumables())
    stores()
    returns()
    field_reads()
    arguments()
    asyncio.run(resumables())
    optionals()
    tuples()
    dict_items()
    walrus()
    set_global()
    print("global", use(G))
    ternary(True)
    ternary(False)


main()
