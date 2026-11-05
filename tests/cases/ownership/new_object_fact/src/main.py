# Whether a value is a new object or shared storage, at every consumer: a call's
# signature, a `cast`'s argument and a tuple result's lent element decide it.
import asyncio
from typing import Any, Iterator, Protocol, cast
from tpy import int32, dynamic, Own, Ptr
from tplib import Box

type Tree = int32 | list[Tree]
type V = None | int32 | list[V]


@dynamic
class Pet(Protocol):
    def bump(self) -> None: ...
    def val(self) -> int32: ...


class Dog:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 5

    def val(self) -> int32:
        return self.n


class Shelf[T]:
    box: Box[T]

    def __init__(self, box: Own[Box[T]]) -> None:
        self.box = box

    def get(self) -> T:
        return self.box.get()


# Free function: the `@dynamic` result returns the stored object (`Pet&`).
def pet_of(b: Box[Pet]) -> Pet:
    return b.get()  # tpyc: ok


# Match arm.
def pick(b: Box[Pet], k: int32) -> Pet:
    match k:
        case 1:
            return b.get()  # tpyc: ok
        case _:
            return b.get()


# try / finally.
def guarded(b: Box[Pet]) -> Pet:
    try:
        return b.get()  # tpyc: ok
    finally:
        print("finally")


# Closure (nested def): the lambda returns `Pet&` as the free function does.
def outer(b: Box[Pet]) -> int32:
    def inner(bb: Box[Pet]) -> Pet:
        return bb.get()  # tpyc: ok
    inner(b).bump()
    return b.get().val()


# Generator: the bound result is the stored object.
def gen_vals(b: Box[Pet]) -> Iterator[int32]:
    p = pet_of(b)
    p.bump()
    yield p.val()


async def coro_val(b: Box[Pet]) -> int32:
    p = pet_of(b)
    p.bump()
    return p.val()


# Async: a frame factory's `Cancellable[T]` is a new frame, held by value.
async def run_frame(b: Box[Pet]) -> int32:
    c = coro_val(b)  # tpyc: ok
    return await c


# Generator / async host: a nested def returning a `@dynamic` borrow is a
# frame member returning `Pet&`, as the free function's lambda does.
def gen_nested(b: Box[Pet]) -> Iterator[int32]:
    def inner(bb: Box[Pet]) -> Pet:
        return bb.get()  # tpyc: ok
    inner(b).bump()
    yield b.get().val()


async def co_nested(b: Box[Pet]) -> int32:
    def inner(bb: Box[Pet]) -> Pet:
        return bb.get()  # tpyc: ok
    inner(b).bump()
    await asyncio.sleep(0)
    return b.get().val()


def passthru(t: Tree) -> Tree:
    return t


def wrap(v: V) -> V:
    return v


def size(t: Tree) -> int32:
    match t:
        case int32():
            return 1
        case list():
            return len(t)


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pair(it: Item) -> tuple[Item, Item]:
    return (it, it)


def keep(xs: list[Item], p: Own[Item]) -> None:
    xs.append(p)


def bump_item(p: Item) -> None:
    p.n += 1


def opt_bump(p: Item | None) -> None:
    if p is not None:
        p.n += 1


def gen_id[T](x: T) -> T:
    return x


# A lent tuple element at an `Own` return is a warned copy, at either index
# spelling.
def ret_last(it: Item) -> Own[Item]:
    return pair(it)[-1]  # tpyc: warning(/copies Item into owned storage/)


@dynamic
class Named(Protocol):
    def label(self) -> str: ...


class Tag:
    def __init__(self) -> None:
        pass

    def label(self) -> str:
        return "k"


# A `@dynamic` method's `str` result is a new value, held once as a key.
def count(n: Named, d: dict[str, int32]) -> None:
    d[n.label()] += 1  # tpyc: ok


class Slot:
    x: Any

    def __init__(self) -> None:
        self.x = 0


def union_bump(p: Item | Slot) -> None:
    if isinstance(p, Item):
        p.n += 1


# A walrus names the binding it makes: storing it into `Any` copies, warned.
def any_store(s: Slot, it: Item) -> None:
    s.x = (w := it)  # tpyc: warning(/copies Item into Any/)
    print("walrus-Any", w.n)


def split(line: str) -> Own[list[str]]:
    parts: list[str] = []
    for i in range(len(line)):
        c = line[i:i + 1]
        # The slice is a new string, so its last use moves it.
        parts.append(c)  # tpyc: ok
    return parts


class Bag:
    items: list[Item]

    def __init__(self) -> None:
        self.items = [Item(0), Item(0)]


def first(c: Bag) -> Item:
    return c.items[0]


def nth(xs: list[Item], i: int32) -> Item:
    return xs[i]


# Method, generator, async body: a lent tuple element and a `cast` at an
# owning sink are warned copies (only the source is read afterwards).
class Keeper:
    xs: list[Item]

    def __init__(self) -> None:
        self.xs = []

    def keep_lent(self, it: Item) -> None:
        self.xs.append(pair(it)[1])  # tpyc: warning(/copies Item into owned storage/)
        self.xs.append(cast(Item, it))  # tpyc: warning(/copies Item into owned storage/)


def gen_lent(it: Item) -> Iterator[int32]:
    xs: list[Item] = []
    xs.append(pair(it)[0])  # tpyc: warning(/copies Item into owned storage/)
    yield len(xs)
    xs.append(cast(Item, it))  # tpyc: warning(/copies Item into owned storage/)
    yield len(xs)


async def co_lent(it: Item) -> int32:
    xs: list[Item] = []
    xs.append(pair(it)[0])  # tpyc: warning(/copies Item into owned storage/)
    await asyncio.sleep(0)
    xs.append(cast(Item, it))  # tpyc: warning(/copies Item into owned storage/)
    return len(xs)


# A `cast` binding of a parameter is the parameter: a write through it
# reaches the caller's object.
def cast_write(it: Item) -> None:
    c = cast(Item, it)  # tpyc: ok
    c.n += 1


# ... and a `cast` returned or yielded is the object itself.
def cast_return(it: Item) -> Item:
    return cast(Item, it)  # tpyc: ok


def cast_yield(it: Item) -> Iterator[Item]:
    yield cast(Item, it)  # tpyc: ok


# A nested def's call is a function call: its reference result is the
# argument, not a new object.
def nested_ref(it: Item) -> None:
    def ident(p: Item) -> Item:
        return p
    ident(it).n += 1  # tpyc: ok


@dynamic
class Tagged(Protocol):
    def tag(self) -> str: ...


class Animal(Tagged):
    legs: int32

    def __init__(self, legs: int32) -> None:
        self.legs = legs

    def tag(self) -> str:
        return "animal"


class Hound(Animal):
    def __init__(self) -> None:
        super().__init__(4)

    def tag(self) -> str:
        return "hound"


class Kennel:
    a: Animal
    box: Box[Animal]

    def __init__(self, n: int32, box: Own[Box[Animal]]) -> None:
        self.a = Animal(n)
        self.box = box


KENNEL = Kennel(1, Box(Hound()))


# A polymorphic subject rooted at a rebound (pointer) local binds the field
# and the accessor's object themselves; the Hound takes the Hound arm.
def poly_rebound() -> None:
    k = Kennel(1, Box(Hound()))
    k = Kennel(2, Box(Hound()))
    match k.a:  # tpyc: ok
        case Hound():
            print("poly-rebound hound")
        case Animal() as a:
            a.legs = 9
    match k.box.get():  # tpyc: ok
        case Hound() as h:
            h.legs = 19
        case _:
            print("poly-rebound-call other")
    print("poly-rebound", k.a.legs, k.box.get().legs)


# ... and one rooted at a module global, field and accessor call alike.
def poly_global() -> None:
    match KENNEL.a:  # tpyc: ok
        case Hound():
            print("poly-global hound")
        case Animal() as a:
            a.legs = 8
    match KENNEL.box.get():  # tpyc: ok
        case Hound() as h:
            h.legs = 18
        case _:
            print("poly-global-call other")
    print("poly-global", KENNEL.a.legs, KENNEL.box.get().legs)


# Guarded arms over a narrowed root and a pointer root.
def poly_guarded(k: Kennel | None, q: Kennel) -> None:
    if k is not None:
        match k.box.get():  # tpyc: ok
            case Hound() as h if h.legs > 0:
                h.legs = 5
            case _:
                print("poly-narrowed other")
        print("poly-narrowed", k.box.get().legs)
    p: Ptr[Kennel] = q
    match p.box.get():  # tpyc: ok
        case Hound() as h2 if h2.legs > 0:
            h2.legs = 3
        case _:
            print("poly-ptr other")
    match p.a:  # tpyc: ok
        case Animal() as a if a.legs > 0:
            a.legs = 2
        case _:
            print("poly-ptr-field other")
    print("poly-ptr", q.box.get().legs, q.a.legs)


# Generator / async host: a nested def returning a plain record hands back
# the argument itself (`Item&`), so a write through the call reaches it.
def gen_nested_record(it: Item) -> Iterator[int32]:
    def ref(p: Item) -> Item:
        return p
    ref(it).n = 42  # tpyc: ok
    yield it.n


async def co_nested_record(it: Item) -> int32:
    def ref(p: Item) -> Item:
        return p
    ref(it).n = 43  # tpyc: ok
    await asyncio.sleep(0)
    return it.n


class Pen:
    k: Kennel

    def __init__(self) -> None:
        self.k = Kennel(1, Box(Hound()))

    @property
    def kennel(self) -> Kennel:
        return self.k

    # Method: a polymorphic subject through a `@property` hop binds the
    # stored object, so the capture's write reaches `self.k.a`.
    def poly_prop_method(self) -> None:
        match self.kennel.a:  # tpyc: ok
            case Hound():
                print("poly-prop-method hound")
            case Animal() as a:
                a.legs = 6
        print("poly-prop-method", self.k.a.legs)

    # Method: a reference result over a durable argument binds the
    # element itself.
    def first_method(self, c: Bag) -> None:
        match first(c):  # tpyc: ok
            case Item() as it:
                it.n = 21
        print("record-call-method", c.items[0].n)


def animal_at(k: Kennel, i: int32) -> Tagged:
    return k.a


def make_item() -> Own[Item]:
    return Item(4)


def bump_first(c: Bag) -> bool:
    c.items[0].n = 2
    return False


def subjects_d() -> None:
    pen = Pen()
    # Free function: a `@property` read returning a stored reference is a
    # hop of the subject place, with or without an accessor after it.
    match pen.kennel.a:  # tpyc: ok
        case Hound():
            print("poly-prop hound")
        case Animal() as a:
            a.legs = 7
    match pen.kennel.box.get():  # tpyc: ok
        case Hound() as h:
            h.legs = 17
        case _:
            print("poly-prop-call other")
    print("poly-prop", pen.k.a.legs, pen.k.box.get().legs)
    pen.poly_prop_method()
    # Guarded arm and loop body over the same hop.
    for _ in range(2):
        match pen.kennel.a:  # tpyc: ok
            case Animal() as a if a.legs > 0:
                a.legs += 1
            case _:
                print("poly-prop-guarded other")
    print("poly-prop-loop", pen.k.a.legs)
    # The guarded record tier binds a reference result over a durable
    # argument as the unguarded one does.
    c = Bag()
    match first(c):  # tpyc: ok
        case Item() as it if it.n >= 0:
            it.n = 8
        case _:
            print("record-guarded other")
    print("record-guarded-call", c.items[0].n)
    pen.first_method(c)
    # Loop body: the same binding on every iteration.
    for _ in range(2):
        match first(c):  # tpyc: ok
            case Item() as it:
                it.n += 1
    print("record-call-loop", c.items[0].n)
    # A polymorphic reference result whose other argument holds no pointer
    # needs no root for it: the capture writes the stored animal.
    match animal_at(pen.k, 0):  # tpyc: ok
        case Hound():
            print("poly-scalar-arg hound")
        case Animal() as a:
            a.legs = 11
        case _:
            pass
    print("poly-scalar-arg", pen.k.a.legs)
    # An extraction out of `Any` is a new object the dispatch local owns
    # (a write through it misses the cell: BUGS.md#any-cast-extracts-copy-silently,
    # so only the capture is read).
    anyv: Any = Item(3)
    match cast(Item, anyv):  # tpyc: ok
        case Item() as ai:
            ai.n += 1
            print("any-cast", ai.n)
    # A new object is owned by the dispatch local.
    match make_item():  # tpyc: ok
        case Item() as m:
            m.n += 1
            print("record-new", m.n)
    # A scalar read off a call is a snapshot: the guard's write to the
    # storage does not reach the later arms.
    match first(c).n:  # tpyc: ok
        case _ if bump_first(c):
            print("never")
        case 2:
            print("scalar-guarded changed")
        case _:
            print("scalar-guarded snapshot", c.items[0].n)


def subjects(b: Box[Pet]) -> None:
    c = Bag()
    # A reference result over a durable argument binds the subject itself.
    match first(c):  # tpyc: ok
        case Item() as it:
            it.n = 5
    print("match-bind", c.items[0].n)
    # A `@dynamic` accessor's result binds by reference too.
    match b.get():  # tpyc: ok
        case Dog() as d:
            d.bump()
        case _:
            pass
    print("match-dynamic", b.get().val())
    # A scalar read off the call is copied into the dispatch local.
    match first(c).n:  # tpyc: ok
        case 5:
            c.items[0].n = 6
            print("match-scalar", c.items[0].n)
        case _:
            print("match-scalar other")
    # A scalar argument carries no reference: only `xs` roots the subject.
    xs = [Item(0), Item(0)]
    match nth(xs, 1):  # tpyc: ok
        case Item() as it2:
            it2.n = 7
    print("match-scalar-arg", xs[1].n)
    # A `cast` subject is its argument: the capture writes `x`.
    x = Item(1)
    match cast(Item, x):  # tpyc: ok
        case Item() as y:
            y.n = 9
    print("match-cast", x.n)
    poly_rebound()
    poly_global()
    subjects_d()
    poly_guarded(Kennel(1, Box(Hound())), Kennel(1, Box(Hound())))
    it3 = Item(1)
    print("generator-nested-record", [v for v in gen_nested_record(it3)],
          it3.n)
    print("async-nested-record", asyncio.run(co_nested_record(it3)), it3.n)


def elements() -> None:
    it = Item(1)
    xs: list[Item] = []
    # A lent tuple element at an owning sink is a warned copy, not a move
    # out of `it` (the copy is the warned divergence, so only `it` is read).
    xs.append(pair(it)[1])  # tpyc: warning(/copies Item into owned storage/)
    print("lent-sink", it.n, len(xs))
    keep(xs, pair(it)[0])  # tpyc: warning(/copies Item into owned storage/)
    print("lent-arg", it.n, len(xs))
    print("lent-return", ret_last(it).n, it.n)
    k = Keeper()
    k.keep_lent(it)
    print("lent-method", it.n, len(k.xs))
    print("lent-generator", [v for v in gen_lent(it)], it.n)
    print("lent-async", asyncio.run(co_lent(it)), it.n)
    # Comprehension sink: the element is copied into the new list, warned.
    ys = [pair(it)[0] for _ in range(2)]  # tpyc: warning(/copies Item into owned storage/)
    print("lent-comprehension", it.n, len(ys))
    # `cast` hands back its argument: a warned copy at a sink, an alias at a
    # binding.
    xs.append(cast(Item, it))  # tpyc: warning(/copies Item into owned storage/)
    print("cast-sink", it.n, len(xs))
    c = cast(Item, it)  # tpyc: ok
    c.n = 15
    print("cast-bind", it.n)
    # A `cast` tuple element is held by reference, as the name is.
    t = (cast(Item, it), 2)  # tpyc: ok
    t[0].n += 1
    print("cast-tuple", it.n, t[0].n)
    cast_write(it)
    print("cast-param", it.n)
    cast_return(it).n += 1
    print("cast-return", it.n)
    for y in cast_yield(it):
        y.n += 1
    print("cast-yield", it.n)
    # A `cast` alias keeps its source from moving: the later sink copies,
    # warned, and the alias still reaches the source.
    a = Item(3)
    ca = cast(Item, a)
    keep(xs, a)  # tpyc: warning(/copies Item into owned storage/)
    ca.n += 1
    print("cast-alias-move", a.n, ca.n)
    nested_ref(it)
    print("nested-call", it.n)
    # ... and an alias at a by-reference argument, a pointer-form Optional
    # argument, a generic argument and a select arm: the write reaches `it`.
    bump_item(cast(Item, it))  # tpyc: ok
    print("cast-arg", it.n)
    opt_bump(cast(Item, it))  # tpyc: ok
    print("cast-opt-arg", it.n)
    union_bump(cast(Item, it))  # tpyc: ok
    print("cast-union-arg", it.n)
    g = gen_id(cast(Item, it))  # tpyc: ok
    g.n += 1
    print("cast-generic-arg", it.n)
    other = Item(0)
    r = cast(Item, it) if len(xs) > 0 else other  # tpyc: ok
    r.n += 1
    print("cast-select", it.n, other.n)
    # At an `Own` argument it is the warned copy (only `it` is read).
    keep(xs, cast(Item, it))  # tpyc: warning(/copies Item into owned storage/)
    print("cast-own-arg", it.n, len(xs))
    d: dict[str, int32] = {"k": 0}
    count(Tag(), d)
    print("dyn-str-key", d["k"])
    print("slice-move", split("ab"))
    any_store(Slot(), it)
    subjects(Box[Pet](Dog(1)))


def main() -> None:
    b: Box[Pet] = Box(Dog(1))
    p = pet_of(b)
    p.bump()
    print("free", b.get().val())
    pick(b, 1).bump()
    print("match", b.get().val())
    guarded(b).bump()
    print("try", b.get().val())
    # `p`'s loan on `b` outlives its last use
    # (BUGS.md#reference-result-loan-outlives-last-use).
    print("closure", outer(b))  # tpyc: warning(/Passing borrowed container 'b'/)
    for v in gen_vals(b):  # tpyc: warning(/Passing borrowed container 'b'/)
        print("generator", v, b.get().val())
    print("async", asyncio.run(run_frame(b)), b.get().val())  # tpyc: warning(/Passing borrowed container 'b'/)
    for v in gen_nested(b):  # tpyc: warning(/Passing borrowed container .b./)
        print("generator-nested", v)
    print("async-nested", asyncio.run(co_nested(b)))  # tpyc: warning(/Passing borrowed container .b./)
    print("comprehension", [pick(b, i).val() for i in range(2)])
    # Method: a generic accessor at `T = Pet`.
    s = Shelf(Box[Pet](Dog(1)))
    s.get().bump()
    print("method", s.box.get().val())
    # A recursive wrapper union result is the argument itself (`Tree&`).
    t: Tree = [1, 2]
    match passthru(t):
        case list() as xs:
            xs.append(3)
        case _:
            pass
    print("wrapper-union", size(t))
    # A wrapper-union passthrough binds the argument itself, as a record's does.
    tmp: V = [1, 2]
    w = wrap(tmp)  # tpyc: ok
    if isinstance(w, list):
        w.append(3)
    if isinstance(tmp, list):
        print("wrapper-union-bind", len(tmp))
    elements()


main()
