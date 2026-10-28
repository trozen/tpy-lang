# A walrus binds a borrowing source as the plain binding `p = src` does: the
# target refers to the existing object, so a write through it reaches the
# owner. One section per position; each mutates through the target, then
# reads the owner.
import asyncio
from typing import Iterator, Protocol

from tpy import Own, ReturnException, deref, dynamic, error_return, int32
from tplib.box import Box
from tplib.rc import Rc


class Animal:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def grow(self) -> int32:
        self.n += 3
        return self.n


class Hound(Animal):
    def __init__(self) -> None:
        super().__init__()


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


@dynamic
class Pet(Protocol):
    def bump(self) -> None: ...
    def count(self) -> int32: ...


class Dog(Pet):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n += 1

    def count(self) -> int32:
        return self.n


class Holder:
    a: Animal
    items: list[int32]

    def __init__(self) -> None:
        self.a = Animal()
        self.items = [1, 2]

    def view(self) -> list[int32]:
        return self.items

    # method: a field of `self`
    def meth(self) -> None:
        if (f := self.a).n > 0:  # tpyc: ok
            f.n += 10
        print("method", self.a.n)


class E(Exception, ReturnException):
    pass


class Ctx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def make_mixed(n: int32, b: Animal) -> tuple[Own[Animal], Animal]:
    a = Animal()
    a.n = n
    return (a, b)


# A source that borrows from a temporary argument (`(s := pick(A(9), b))`)
# is refused, as the plain binding of it is where the target outlives the
# statement (calls/error_borrowed_call_ternary_argument pins that refusal).
def free_function() -> None:
    # free function, inheritance-polymorphic payload behind deref
    rc: Rc[Animal] = Rc.new(Hound())
    if (p := deref(rc)).n > 0:  # tpyc: ok
        p.n += 10
    print("free deref", deref(rc).n)
    # free function, Box.get
    b = Box(Point(1))
    if (q := b.get()).x > 0:  # tpyc: ok
        q.x += 10
    print("free box", b.get().x)
    # free function, list element
    xs = [Animal(), Animal()]
    i = 1
    if (e := xs[i]).n > 0:  # tpyc: ok
        e.n += 10
    print("free elem", xs[1].n)
    # free function, field
    h = Holder()
    if (f := h.a).n > 0:  # tpyc: ok
        f.n += 10
    print("free field", h.a.n)
    # free function, @dynamic payload and a virtual call on the walrus
    pets: Rc[Pet] = Rc.new(Dog())
    if (d := deref(pets)).count() >= 0:  # tpyc: ok
        d.bump()
    print("free dyn", deref(pets).count())


def gen_owned_elem(v: Animal, c: bool) -> Iterator[int32]:
    # generator: the owned element of a rebound tuple; a rebind of the
    # tuple gives the new element storage of its own, so the alias keeps
    # the old object
    p = make_mixed(1, v)
    if (a := p[0]).n > 0:  # tpyc: ok
        yield 0
    if c:
        p = make_mixed(9, v)
    a.n += 100
    yield a.n
    yield p[0].n


def gen(h: Holder, v: Animal, c: bool) -> Iterator[int32]:
    # generator: a field, written after a suspension
    if (f := h.a).n > 0:  # tpyc: ok
        yield 0
    f.n += 100
    # generator: an element of a rebound pointer tuple
    t = make_mixed(1, v)
    if (a := t[1]).n > 0:  # tpyc: ok
        yield 1
    if c:
        t = make_mixed(9, v)
    a.n += 1000
    yield a.n


async def co(h: Holder) -> None:
    # async: a field, written after an await
    if (f := h.a).n > 0:  # tpyc: ok
        await asyncio.sleep(0)
    f.n += 7
    print("async", h.a.n)


def closure() -> None:
    h = Holder()

    def inner() -> None:
        # nested def: a field of a captured object
        if (f := h.a).n > 0:  # tpyc: ok
            f.n += 20
    inner()
    print("closure", h.a.n)


def try_finally(h: Holder) -> int32:
    # try/finally: the target is hoisted ahead of the `try`
    try:
        if len(v := h.view()) > 0:  # tpyc: ok
            v.append(9)
    finally:
        print("try/finally", len(h.items))
    return len(h.items)


def try_scalar(n: int32) -> None:
    # try/finally: a scalar walrus in the try body, another in the finally
    try:
        if (t := n + 1) > 1:  # tpyc: ok
            print("try scalar", t)
    finally:
        if (u := n + 2) > 1:  # tpyc: ok
            print("finally scalar", u)


def gen_try(h: Holder) -> Iterator[int32]:
    # generator try/finally: the walrus target lives across the yield
    try:
        if (f := h.a).n > 0:  # tpyc: ok
            yield 1
        f.n += 5
    finally:
        print("generator finally", h.a.n)


async def co_try(h: Holder) -> None:
    # async try/finally
    try:
        if (f := h.a).n > 0:  # tpyc: ok
            await asyncio.sleep(0)
        f.n += 6
    finally:
        print("async finally", h.a.n)


def try_except_return(h: Holder) -> int32:
    # try/except: the hoisted target, with an early return in the handler
    try:
        if len(v := h.view()) > 0:  # tpyc: ok
            v.append(9)
    except ValueError:
        return -1
    return len(h.items)


def try_owned() -> None:
    # try/finally: a FRESH object into the hoist, which owns it
    try:
        if (p := Animal()).n > 0:  # tpyc: ok
            p.n += 3
    finally:
        print("try owned finally")
    print("try owned", p.n)


def with_body() -> None:
    h = Holder()
    with Ctx():
        # with body: a field
        if (f := h.a).n > 0:  # tpyc: ok
            f.n += 70
    print("with", h.a.n)


@error_return(E)
def er_body(h: Holder) -> int32:
    # @error_return body: a field
    if (f := h.a).n > 0:  # tpyc: ok
        f.n += 80
    return h.a.n


class Res:
    n: int32

    def __init__(self) -> None:
        self.n = 3

    def __del__(self) -> None:
        print("del", self.n)


class OptHolder:
    opt: Animal | None

    def __init__(self) -> None:
        self.opt = Animal()


def gen_last_use() -> Iterator[int32]:
    q = Res()
    # generator: a name at its last use; the frame field aliases it as
    # `p = q` there does, so nothing copies the `__del__` object
    if (p := q).n > 0:  # tpyc: ok
        yield p.n
    p.n += 1
    yield p.n


async def co_last_use() -> None:
    q = Res()
    # async: the same, across an await
    if (p := q).n > 0:  # tpyc: ok
        await asyncio.sleep(0)
    p.n += 1
    print("async last use", p.n)


def try_opt_field() -> None:
    h = OptHolder()
    try:
        # the hoisted pointer-repr Optional lifts the stored field, as the
        # plain assignment there does; the walrus reads back the pointer
        if (p := h.opt) is not None:  # tpyc: ok
            p.n += 1
    finally:
        print("try opt field finally")
    if p is not None:
        p.n += 1
    print("try opt field", h.opt.n if h.opt is not None else 0)


def branch_fresh(c: bool) -> None:
    if c:
        # a branch-first fresh object fills the slot of its own the
        # hoisted pointer aims at
        if (p := Animal()).n > 0:  # tpyc: ok
            p.n += 6
    else:
        p = Animal()
    print("branch fresh", p.n)


def ident(a: Animal) -> Animal:
    return a


def temp_source() -> None:
    # a first binding over a call that borrows its temporary argument: the
    # temporary lives as long as its statement, as for `p = ident(Animal())`
    if (p := ident(Animal())).n > 0:  # tpyc: warning(/Result borrows from temporary argument/)
        p.n += 4
        print("temp source", p.n)


def try_fresh_then_borrow() -> None:
    a = Animal()
    try:
        # a fresh object into the `try` hoist, then a later plain rebind to
        # an existing one: each write as the plain assignment's
        if (p := Animal()).n > 0:  # tpyc: ok
            p.n += 10
        p = a
    finally:
        print("try fresh then borrow finally")
    p.n += 10
    print("try fresh then borrow", p.n, a.n)


def try_name_moves(k: int32) -> None:
    try:
        q = Animal()
        q.n = k
        # the name source at its last use hands its object over, as
        # `p = q` there does
        if (p := q).n > 0:  # tpyc: ok
            print("try name moves in", p.n)
    finally:
        print("try name moves finally")
    p.n += 10
    print("try name moves", p.n)


def over_hoisted_owner(k: int32) -> None:
    try:
        q = Animal()
        q.n = k
    finally:
        print("over hoisted owner finally")
    # a first binding over a name the `try` hoisted into owned storage
    if (p := q).n > 0:  # tpyc: ok
        p.n += 1
    print("over hoisted owner", q.n, p.n)


def name_moves() -> None:  # tpyc: mir(uncovered /reference needs local name/)
    q = Animal()
    # a name at its last use moves into the walrus target, as `p = q` does
    if (p := q).n > 0:  # tpyc: ok
        p.n += 5
    print("name moves", p.n)


def borrow_then_own() -> None:
    h = Holder()
    # the walrus borrows; a later plain rebind gives `p` a fresh object of
    # its own, and the field keeps the write made through the walrus
    if (p := h.a).n > 0:  # tpyc: ok
        p.n += 7
    p = Animal()
    p.n = 20
    print("borrow then own", h.a.n, p.n)


def match_arm(k: int32) -> None:
    h = Holder()
    match k:
        case 1:
            # match arm: a field
            if (f := h.a).n > 0:  # tpyc: ok
                f.n += 30
        case _:
            pass
    print("match", h.a.n)


def lazy(c: bool) -> None:
    rc: Rc[Animal] = Rc.new(Animal())
    # lazy `and` operand: bound only when `c` holds
    if c and (p := deref(rc)).n > 0:  # tpyc: ok
        p.n += 40
    print("lazy and", c, deref(rc).n)
    xs = [Animal(), Animal()]
    # ternary arm: bound, and written through, only when `c` holds
    k = (q := xs[1]).grow() if c else 0  # tpyc: ok
    print("lazy ternary", k, xs[1].n)


def returned(h: Holder) -> Animal:
    # returning the walrus target: the caller gets the object itself
    if (f := h.a).n > 0:  # tpyc: ok
        return f
    return h.a


def main() -> None:
    free_function()
    Holder().meth()
    h = Holder()
    v = Animal()
    print("generator", [x for x in gen(h, v, True)], h.a.n, v.n)
    for x in gen_owned_elem(Animal(), True):
        print("generator owned elem", x)
    hg = Holder()
    for _ in gen_try(hg):
        pass
    asyncio.run(co_try(Holder()))
    try_scalar(3)
    print("try/except", try_except_return(Holder()))
    try_owned()
    with_body()
    try:
        er = er_body(Holder())
    except E:
        er = -1
    print("error_return", er)
    borrow_then_own()
    temp_source()
    try_fresh_then_borrow()
    try_name_moves(3)
    over_hoisted_owner(3)
    name_moves()
    for x in gen_last_use():
        print("generator last use", x)
    asyncio.run(co_last_use())
    try_opt_field()
    branch_fresh(True)
    h2 = Holder()
    asyncio.run(co(h2))
    closure()
    r = try_finally(Holder())
    print("try/finally result", r)
    match_arm(1)
    lazy(True)
    lazy(False)
    h3 = Holder()
    returned(h3).n += 50
    print("returned", h3.a.n)


main()

# module level: a borrowing source bound by a walrus at the top level
mh = Holder()
if (mf := mh.a).n > 0:  # tpyc: ok
    mf.n += 60
print("module", mh.a.n)
