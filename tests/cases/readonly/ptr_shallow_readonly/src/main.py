# readonly is shallow through Ptr and Span: const protects the storage that
# holds the handle, not what it points at. A method that only writes through
# a Ptr / Span field is inferred const; a Ptr[readonly[T]] / Span[readonly[T]]
# protects the referent. readonly over a copy holding no reference (a value
# instantiation, a handle) is dropped.
import asyncio
from dataclasses import dataclass
from typing import Iterator, Protocol
from tpy.mem import UninitStorage
from tpy import (Ptr, Span, Array, int32, readonly, auto_readonly, pure, error_return,
                 ReturnException, copy, dynamic)


class Err(Exception, ReturnException):
    pass


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


class Pair:
    first: A
    items: list[A]

    def __init__(self) -> None:
        self.first = A(200)
        self.items = [A(300)]


class Slot:
    x: A | None

    def __init__(self) -> None:
        self.x = A(400)


class M:
    _a: Ptr[A]
    _p: Ptr[Pair]
    _s: Ptr[Slot]
    own: A
    xs: Span[int32]
    ps: list[Ptr[A]]

    # ctor: the params stored into Ptr fields stay mutable references
    def __init__(self, a: A, p: Pair, s: Slot, xs: Span[int32]) -> None:
        self._a = a
        self._p = p
        self._s = s
        self.own = A(100)
        self.xs = xs
        self.ps = []

    # inline: writes only through the Ptr field -> const
    def tick(self) -> None:  # tpyc: ok
        self._a.bump()
        self._a.n += 10

    # alias: a Ptr copied out of the field is the same pointer
    def tick_alias(self) -> None:
        a = self._a
        a.bump()

    # place_alias: references bound to places behind the Ptr, written
    # through (a field, an element, a loop variable) -> const
    def through_places(self) -> None:
        it = self._p.first
        it.bump()
        y = self._p.items[0]
        y.bump()
        q = self._p
        for x in q.items:
            x.bump()

    # optional: an Optional reached through the Ptr, narrowed and written
    def poke_opt(self) -> None:
        o = self._s.x
        if o is not None:
            o.bump()

    # span_field: writes only through the Span field -> const
    def poke(self) -> None:  # tpyc: ok
        self.xs[0] += 1

    # elements_field: a loop over a list of Ptr writes the pointees only
    def tick_all(self) -> None:
        for p in self.ps:
            p.bump()

    # return: the pointee at a mutable return slot
    def get(self) -> A:
        a = self._a
        return a

    # mixed: one path returns self's own storage mutably -> not const
    def pick(self, k: int32) -> A:
        if k > 0:
            a = self._a
            return a
        return self.own

    # declared: an explicit @readonly may hand out the pointee mutably
    @readonly
    def peek(self) -> A:
        p = self._a
        return p  # tpyc: ok

    # declared readonly own storage needs a readonly return type
    @readonly
    def own_ro(self) -> readonly[A]:
        return self.own

    # implicit: a @pure method declares its borrowed return readonly
    @pure
    def own_pure(self) -> A:
        return self.own  # tpyc: ok

    # closure: a nested def writing through the Ptr field
    def via_closure(self) -> None:
        def go() -> None:
            self._a.bump()
        go()

    # generator: a generator body writing through the Ptr field
    def bump_each(self, k: int32) -> Iterator[int32]:
        for _ in range(k):
            self._a.bump()
            yield self._a.n

    # async: a coroutine body writing through the Ptr field
    async def tick_async(self) -> int32:
        self._a.bump()
        return self._a.n

    # error_return: the pointee through an @error_return method
    @error_return(Err)
    def get_or_fail(self, ok: bool) -> A:
        if not ok:
            raise Err()
        a = self._a
        return a


# declared_bound: the pointee an explicit @readonly method hands out, bound
# and written through at each position; the receiver stays const
def peek_local(m: M) -> None:
    x = m.peek()  # tpyc: ok
    x.n += 1


def peek_walrus(m: M) -> None:
    if (x := m.peek()).n >= 0:  # tpyc: ok
        x.n += 1


def peek_match(m: M, k: int32) -> None:
    match k:
        case 0:
            x = m.peek()  # tpyc: ok
            x.n += 1
        case _:
            pass


def peek_closure(m: M) -> None:
    def inner() -> None:
        x = m.peek()  # tpyc: ok
        x.n += 1
    inner()


def peek_branch(m: M, other: M, c: bool) -> None:
    if c:
        x = m.peek()  # tpyc: ok
    else:
        x = other.peek()
    x.n += 1


def peek_ro(m: readonly[M]) -> None:
    x = m.peek()  # tpyc: ok
    x.n += 1


# field_readonly_ptr: readonly on a Ptr field protects the slot (it cannot be
# re-pointed), not what it points at
class Fixed:
    ro: readonly[Ptr[A]]

    def __init__(self, a: A) -> None:
        self.ro = a

    def poke(self) -> None:
        self.ro.n += 1
        p = self.ro
        p.bump()


# ctor_write: a constructor writing through the Ptr parameter it stores
class Starter:
    _a: Ptr[A]

    def __init__(self, a: Ptr[A]) -> None:
        self._a = a
        a.bump()


# generic: an open-T Ptr[T] holder, the monomorphic twin of M.get
class PBox[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p

    def get(self) -> T:
        q = self.p
        return q

    @auto_readonly
    def ga(self) -> auto_readonly[T]:
        q = self.p
        return q


# generic_local: a T local bound off a receiver-following call in a method
# inferred const binds the const trait, as its monomorphic twin binds const
class PHold[T]:
    b: PBox[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.b = PBox[T](p)

    def touch(self) -> int32:
        x = self.b.ga()  # tpyc: ok
        return 1


class PHoldA:
    b: PBox[A]

    def __init__(self, p: Ptr[A]) -> None:
        self.b = PBox[A](p)

    def touch(self) -> int32:
        x = self.b.ga()  # tpyc: ok
        return x.n


class Key:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1

    def __eq__(self, o: "Key") -> bool:
        return self.n == o.n

    def __hash__(self) -> int:
        return hash(self.n)


# implicit_frozen: a frozen record's method declares its borrowed return
# readonly
@dataclass(frozen=True)
class Frozen:
    k: Key

    def first(self) -> Key:
        return self.k  # tpyc: ok

    # frozen_optout: @readonly(False) / @auto_readonly hand out a mutable borrow
    @readonly(False)
    def mut(self) -> Key:
        return self.k  # tpyc: ok

    @auto_readonly
    def pick(self) -> auto_readonly[Key]:
        return self.k  # tpyc: ok


# varargs: an element of an unmutated *args pack, emitted over const
# elements, bound at each position
def va_local(*args: A) -> int32:
    x = args[0]  # tpyc: ok
    y: A = args[1]  # tpyc: ok
    return x.n + y.n


def va_closure(*args: A) -> int32:
    x = args[0]  # tpyc: ok

    def g() -> int32:
        return x.n
    return g()


def va_match(*args: A) -> int32:
    match args[0].n:
        case 0:
            return -1
        case _:
            x = args[0]  # tpyc: ok
            return x.n


def va_unpack(*args: A) -> int32:
    a, b = args[0], args[1]  # tpyc: ok
    return a.n + b.n


# varargs_write: a pack the body writes through is emitted over mutable
# elements, at the same positions
def va_write(*args: A) -> None:
    x = args[0]  # tpyc: ok
    x.n += 1
    y: A = args[1]  # tpyc: ok
    y.n += 10
    a, b = args[0], args[1]  # tpyc: ok
    a.n += 100
    b.n += 100


class Va:
    k: int32

    def __init__(self) -> None:
        self.k = 1

    def first(self, *args: A) -> int32:
        x = args[0]  # tpyc: ok
        return x.n + self.k


# value_result: readonly over a value instantiation is the copy itself
class GV[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = copy(v)

    def __add__(self, o: int32) -> T:
        return self.v

    async def aread(self) -> readonly[T]:
        return self.v

    @pure
    def get(self) -> T:
        return self.v

    def rget(self) -> readonly[T]:
        return self.v


def value_sum(g: GV[int32]) -> int32:
    x = g + 1
    y = g + 2
    return x + y  # tpyc: ok


def value_methods(g: GV[int32]) -> int32:
    x = g.rget()
    y = g.get()
    return x + y  # tpyc: ok


def value_total(d: readonly[dict[int32, int32]]) -> int32:
    t = 0
    for v in d.values():
        t += v  # tpyc: ok
    for k, w in d.items():
        t += k + w  # tpyc: ok
    return t


async def value_await(g: GV[int32]) -> int32:
    x = await g.aread()
    y = await g.aread()
    return x + y  # tpyc: ok


# value_protocol: a protocol method declaring `-> readonly[T]`, used at
# T = int32, returns the copy, static and @dynamic alike
class Getter[T](Protocol):
    def get(self) -> readonly[T]: ...


@dynamic
class DGetter[T](Protocol):
    def get(self) -> readonly[T]: ...


class IntSrc:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


def value_proto(p: Getter[int32]) -> int32:
    x = p.get()
    y = p.get()
    return x + y  # tpyc: ok


def value_dyn(p: DGetter[int32]) -> int32:
    x = p.get()
    y = p.get()
    return x + y  # tpyc: ok


# shim: the operator shim of a tuple-returning dunder spells the method's own
# return, a readonly element included; `()` is called and written through
# (a `+` / `*` call of a tuple-returning dunder does not lower,
# BUGS.md#tuple-result-operator-call-rejects)
class Sh:
    a: A

    def __init__(self) -> None:
        self.a = A(3)

    def __add__(self, o: int32) -> tuple[A, int32]:  # tpyc: ok
        return (self.a, o)

    def __rmul__(self, o: int32) -> tuple[readonly[A], int32]:  # tpyc: ok
        return (self.a, o)

    def __call__(self, o: int32) -> tuple[A, int32]:  # tpyc: ok
        self.a.n += 1
        return (self.a, o)


# implicit_tuple: a @pure / frozen method declares the borrowed element of its
# tuple result readonly, element by element
class G:
    a: A

    def __init__(self) -> None:
        self.a = A(1)

    @pure
    def both(self) -> tuple[A, int32]:
        return (self.a, 2)  # tpyc: ok


@dataclass(frozen=True)
class FrozenPair:
    k: Key

    def both(self) -> tuple[Key, int32]:
        return (self.k, 3)  # tpyc: ok


# span_field_readonly: readonly on a Span field protects the slot, not the
# elements it views
class V:
    xs: readonly[Span[int32]]

    def __init__(self, xs: Span[int32]) -> None:
        self.xs = xs

    def poke(self) -> None:  # tpyc: ok
        self.xs[0] += 1


def poke_ro(v: readonly[V]) -> None:
    v.xs[1] += 10


# union_through_ptr: a union read through a Ptr field is the pointee's, so a
# const method still writes the member it narrows to
class Leaf:
    m: int32

    def __init__(self) -> None:
        self.m = 0


class UPayload:
    u: A | Leaf

    def __init__(self) -> None:
        self.u = A(0)


class UH:
    p: Ptr[UPayload]

    def __init__(self, p: UPayload) -> None:
        self.p = p

    def bump_match(self) -> None:
        match self.p.u:
            case A() as a:
                a.n += 10  # tpyc: ok
            case Leaf() as b:
                b.m += 1

    def bump_local(self) -> None:
        u = self.p.u
        if isinstance(u, A):
            u.n += 100  # tpyc: ok


# stub_get: a method returning what a bodyless storage stub hands out, at a
# record T (the monomorphic twin of the generic stubs)
class Stored:
    s: UninitStorage[A]

    def __init__(self, v: int32) -> None:
        self.s = UninitStorage[A]()
        self.s.construct(A(v))

    def get(self) -> A:
        return self.s.get()  # tpyc: ok


# async_payload: an async method's readonly[T] result at a record T
class RBox[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = copy(v)

    @readonly
    async def peek(self) -> readonly[T]:
        return self.val


async def peek_n(b: RBox[A]) -> int32:
    p = await b.peek()  # tpyc: ok
    return p.n


# readonly_param: a readonly receiver still writes what its handles point at
def via_ro(m: readonly[M]) -> None:
    m._a.n += 1000
    m.xs[1] += 1


def use(m: M) -> None:
    m.get().bump()
    m.peek().bump()
    m.pick(1).bump()
    m.pick(0).bump()


# elements: Ptr elements of a readonly list point at mutable objects
def bump_all(ps: readonly[list[Ptr[A]]]) -> None:
    for p in ps:
        p.bump()


# elements_mutable: the same over a plain list keeps the list const
def bump_list(ps: list[Ptr[A]]) -> None:
    for p in ps:
        p.n += 1


# error_return_bind: the caller binds the pointee and writes through it
def bump_or_zero(m: M) -> int32:
    try:
        x = m.get_or_fail(True)
        x.bump()
        return x.n
    except Err:
        return 0


def main() -> None:
    objs = [A(0), A(50)]
    ns: Array[int32, 2] = [1, 2]
    pair = Pair()
    slot = Slot()
    m = M(objs[0], pair, slot, ns)
    m.tick()
    m.tick_alias()
    m.poke()
    m.via_closure()
    print("inline", objs[0].n, ns[0])
    m.through_places()
    print("place_alias", pair.first.n, pair.items[0].n)
    m.poke_opt()
    o = slot.x
    if o is not None:
        print("optional", o.n)
    use(m)
    print("return", objs[0].n, m.own.n)
    via_ro(m)
    print("readonly_param", objs[0].n, ns[1], m.own_ro().n)
    # implicit: the readonly result aliases the source, which is then mutated
    r = m.own_pure()
    m.own.bump()
    print("implicit", r.n)
    try:
        m.get_or_fail(True).bump()
    except Err:
        print("unreachable")
    print("error_return", objs[0].n)
    print("error_return_bind", bump_or_zero(m))
    total = 0
    for v in m.bump_each(2):
        total += v
    print("generator", total, objs[0].n)
    print("async", asyncio.run(m.tick_async()))
    ps: list[Ptr[A]] = []
    ps.append(objs[1])
    bump_all(ps)
    bump_list(ps)
    m.ps.append(objs[1])
    m.tick_all()
    print("elements", objs[1].n)
    fx = Fixed(objs[1])
    fx.poke()
    print("field_readonly_ptr", objs[1].n)
    Starter(objs[1])
    print("ctor_write", objs[1].n)
    pb = PBox[A](objs[1])
    pb.get().bump()
    print("generic", objs[1].n)
    print("generic_local", PHold[A](objs[1]).touch(), PHoldA(objs[1]).touch())
    da = A(1)
    dp = Pair()
    ds = Slot()
    dm = M(da, dp, ds, ns)
    peek_local(dm)
    peek_walrus(dm)
    peek_match(dm, 0)
    peek_closure(dm)
    peek_branch(dm, dm, True)
    peek_ro(dm)
    print("declared_bound", da.n)
    fr = Frozen(Key(7))
    k = fr.first()
    fr.k.bump()
    print("implicit_frozen", k.n)
    fr.mut().bump()
    p = fr.pick()
    p.bump()
    print("frozen_optout", fr.k.n)
    a1 = A(1)
    a2 = A(2)
    print("varargs", va_local(a1, a2), va_closure(a1, a2), va_match(a1, a2),
          va_unpack(a1, a2), Va().first(a1, a2))
    va_write(a1, a2)
    print("varargs_write", a1.n, a2.n)
    gv = GV[int32](4)
    print("value_result", value_sum(gv), asyncio.run(value_await(gv)),
          value_total({1: 2, 3: 4}), value_methods(gv))
    src = IntSrc(4)
    print("value_protocol", value_proto(src), value_dyn(src))
    sh = Sh()
    called = sh(5)
    sh.a.n = 9
    print("shim", called[0].n, called[1])
    called[0].n = 20
    print("shim_write", sh.a.n)
    g = G()
    t = g.both()
    g.a.bump()
    fp = FrozenPair(Key(4))
    w = fp.both()
    fp.k.bump()
    print("implicit_tuple", t[0].n, t[1], w[0].n, w[1])
    span_ns: Array[int32, 2] = [1, 2]
    sv = V(span_ns)
    sv.poke()
    poke_ro(sv)
    print("span_field_readonly", span_ns[0], span_ns[1])
    up = UPayload()
    uh = UH(up)
    uh.bump_match()
    uh.bump_local()
    uu = up.u
    if isinstance(uu, A):
        print("union_through_ptr", uu.n)
    st = Stored(6)
    st.get().bump()
    print("stub_get", st.get().n)
    print("async_payload", asyncio.run(peek_n(RBox[A](A(5)))))


main()
