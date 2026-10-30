# A call argument converts at the slot its parameter's contract builds, at
# every callee and position; each section names its shape on its subject line.
import asyncio
import heapq
import sys
from enum import Enum
from typing import Callable, Iterator, Optional, Protocol
from tpy import (BytesView, Own, Span, ValueType, copy, deref, dispatch, dynamic,
                 int32,
                 int64, readonly)
from tplib.box import Box
from tplib.rc import Rc


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    def __init__(self, a: Own[A]) -> None:
        self.a = a


def mkh() -> Own[H]:
    return H(A(7))


def ref(a: A) -> None:
    a.v += 3


def two(x: Own[A], n: int32) -> None:  # tpyc: warning(/param 'x' is never consumed/)
    x.v += 0
    print("two", n)


def bump(a: A) -> int32:
    a.v += 10
    return a.v


def os_(x: Own[str]) -> None:
    print("os_", x)


def ns(x: str) -> int32:
    return len(x)


def eats(x: Own[str]) -> int32:
    return len(x)


def mkstr(n: int32) -> str:
    return "ab" * n


class E:
    def take(self, x: Own[str]) -> int32:
        return len(x)


class K:
    def __init__(self, x: Own[str]) -> None:
        self.x = x


class Color(Enum):
    RED = 1
    BLUE = 2


class Keep[T]:
    first: T

    def __init__(self, first: Own[T]) -> None:
        self.first = first


class Bump(Protocol):
    def bump(self) -> None: ...


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 10


class Holder:
    c: Cell

    def __init__(self) -> None:
        self.c = Cell(1)

    def borrow(self) -> Cell:
        return self.c


def poke[T: Bump](v: T) -> None:
    v.bump()


class Inner[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field if not a value type/)

    def put(self, value: T) -> None:
        self.v = value  # tpyc: warning(/may copy T into field if not a value type/)


class Outer[T]:
    _in: Inner[T]

    def __init__(self, v: T) -> None:
        self._in = Inner[T](v)

    def put_own(self, value: Own[T]) -> None:  # tpyc: warning(/param 'value' is never consumed/)
        # own_tparam_fwd: an `Own[T]` parameter at a bare `T` method slot
        self._in.put(value)  # tpyc: ok


def view_set_add(k: BytesView) -> None:
    s: set[BytesView] = set()
    # view_set_add: a BytesView name at a `set[BytesView]` insert
    s.add(k)  # tpyc: ok
    print("view_set_add", len(s))


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


class PHolder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


def maybe_make(x: int32) -> Own[Point] | None:
    if x > 0:
        return Point(x, x)
    return None


def put(h: PHolder, p: Own[Point] | None) -> None:
    h.value = p


def take(x: Own[int32]) -> int32:
    return x


def narrowed(v: int | None) -> None:
    if v is not None:
        # own_scalar_narrowed: a narrowed `int | None` name at `Own[int32]`
        print("own_scalar_narrowed", take(v))  # tpyc: ok


class Big:
    big: int

    def __init__(self) -> None:
        self.big = 7


class Bag[T]:
    held: list[T]

    def __init__(self) -> None:
        self.held = []

    def push(self, x: Own[T]) -> None:
        self.held.append(x)

    def echo(self, src: list[T], i: int32) -> None:
        # own_elem: an element read at a record method's `Own[T]` slot
        self.push(src[i])  # tpyc: warning(/may copy T into owned storage if not a value type/)


@dispatch
def g[T](x: T) -> str:
    return "generic"


@dispatch
def g(x: int64) -> str:
    return "int64"


class CBox[N: int]:
    def run(self) -> None:
        # const_generic: an integer constant generic at the winning `T` slot
        print("const_generic", g(N))  # tpyc: ok


class Sink:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def put(self, b: Own[bytes]) -> None:
        self.n += len(b)


def eat(b: Own[bytes]) -> int32:
    return len(b)


def feed(s: Sink, src: bytes) -> int32:
    # own_bytes: an owned `bytes` local at an `Own[bytes]` parameter moves
    # at its last use, as an `Own[str]` one does; read again later it is
    # copied for the call
    kept: bytes = bytes(src)
    s.put(kept)  # tpyc: ok
    owned: bytes = bytes(src)
    s.put(owned)  # tpyc: ok
    free: bytes = bytes(src)
    n = eat(free)  # tpyc: ok
    print("own_bytes", len(kept), n)
    return s.n


class Vec(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class W:
    v: Vec

    def __init__(self) -> None:
        self.v = Vec(3)


class VH:
    def __init__(self) -> None:
        pass

    def m(self, v: Vec | None = None) -> int32:
        if v is None:
            return -1
        return v.n


def eat_a(a: Own[A]) -> int32:  # tpyc: warning(/param 'a' is never consumed/)
    a.v += 100
    return a.v


class V3:
    x: float

    def __init__(self, x_: float) -> None:
        self.x = x_

    def muls(self, s: float) -> Own["V3"]:
        return V3(self.x * s)

    def absorb(self, v: "V3") -> float:
        v.x += 1.0
        return v.x


class Soak:
    def soak(self, row: list[float]) -> int32:
        row.append(1.0)
        return len(row)

    def fill(self, xs: list[int32]) -> int32:
        xs.append(9)
        return len(xs)


def mk_list() -> Own[list[int32]]:
    return [1, 2]


class Child(A):
    def __init__(self) -> None:
        super().__init__(2)


class HC:
    c: Child

    def __init__(self) -> None:
        self.c = Child()


class Payload:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class PH:
    p: Payload
    items: list[int32]

    def __init__(self) -> None:
        self.p = Payload(42)
        self.items = [1]

    def borrow(self) -> Payload:
        return self.p

    def items_m(self) -> list[int32]:
        return self.items


def keep(p: Own[Payload]) -> Own[Payload]:
    p.v += 1
    return p


class PSink:
    p: Payload

    def __init__(self, p: Own[Payload]) -> None:
        self.p = p

    def replace(self, q: Own[Payload]) -> None:
        self.p = q


class KeepThing:
    t: A

    def __init__(self, t: Own[A]) -> None:
        self.t = t


def eat_strs(xs: Own[list[str]]) -> int:  # tpyc: warning(/param 'xs' is never consumed/)
    return len(xs)


def grow(xs: list[int32]) -> int32:
    xs.append(7)
    return len(xs)


class OptBox:
    items: Optional[list[int32]]

    def __init__(self) -> None:
        self.items = [0]


G: list[int32] = [1, 2]


class GH:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G


def mk_gh() -> Own[GH]:
    return GH()


def take_dict[T](w: T, d: dict[str, int32]) -> int32:
    d["z"] = 9
    return len(d)


@dispatch
def tot3(d: dict[str, float]) -> float:
    return 1.5 + len(d)


@dispatch
def tot3(s: str) -> str:
    return s


def first_byte(b: Own[bytearray]) -> int32:  # tpyc: warning(/param 'b' is never consumed/)
    return b[0]


class UB:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


def take_own_a(o: Own[A]) -> Own[A]:
    o.v += 1
    return o


def use_union(u: A | UB) -> int32:
    if isinstance(u, A):
        # narrowed_union_own: the narrowed read's warned copy into `Own[A]`
        return take_own_a(u).v  # tpyc: warning(/copies A into owned storage/)
    return 0


class Numbers:
    total: int32

    def __init__(self, xs: readonly[list[int32]]) -> None:
        self.total = len(xs)


def reference_args(c: bool) -> None:
    # own_field: a field at an `Own[A]` parameter is the warned copy (only
    # the owned side is printed here; calls/argument_sink_warned_copy prints
    # the source too)
    h = mkh()
    print("own_field", eat_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    # own_temp_member: the member of a temporary moves in, no copy
    print("own_temp_member", eat_a(mkh().a))  # tpyc: ok
    # mutated_rvalue: a call's result at a mutated method parameter is held
    a = V3(1.0)
    b = V3(2.0)
    print("mutated_rvalue", a.absorb(b.muls(3.0)), b.x)  # tpyc: ok
    # mutated_comprehension: the same for a comprehension
    s = Soak()
    print("mutated_comprehension", s.soak([2.0 * float(i) for i in range(3)]))  # tpyc: ok
    # mutated_owning_call: an owning call's container at a mutated parameter
    print("mutated_owning_call", s.fill(mk_list()))  # tpyc: ok
    # field_subclass: a subclass field at its base's parameter aliases it
    hc = HC()
    ref(hc.c)  # tpyc: ok
    print("field_subclass", hc.c.v)
    # own_borrow_call: a borrow-returning call at an `Own` parameter copies
    # (the source is printed in calls/argument_sink_warned_copy)
    ph = PH()
    d = keep(ph.borrow())  # tpyc: warning(/copies Payload into owned storage/)
    print("own_borrow_call", d.v)
    # own_borrow_call_ctor: the same copy at a constructor's `Own` parameter
    ph2 = PH()  # fresh: under CPython keep() above wrote ph.p
    ps = PSink(ph2.borrow())  # tpyc: warning(/copies Payload into owned storage/)
    print("own_borrow_call_ctor", ps.p.v)
    # own_borrow_call_method: the same copy at a method's `Own` parameter
    ps.replace(ph2.borrow())  # tpyc: warning(/copies Payload into owned storage/)
    print("own_borrow_call_method", ps.p.v)
    # own_element: a list element at an `Own` constructor parameter copies
    things = [A(4), A(5)]
    k = KeepThing(things[0])  # tpyc: warning(/copies A into owned storage/)
    k.t.v += 1
    print("own_element", k.t.v)
    # module_var_own: a module's container at an `Own` parameter copies
    print("module_var_own", eat_strs(sys.argv) >= 1)  # tpyc: warning(/copies list\[str\] into owned storage/)
    # borrow_container_ret: a borrow-returning container call binds in place
    print("borrow_container_ret", grow(ph.items_m()), ph.items)  # tpyc: ok
    # narrowed_opt_field: a narrowed Optional field binds its container
    ob = OptBox()
    if ob.items is not None:
        grow(ob.items)  # tpyc: ok
        print("narrowed_opt_field", ob.items)
    # property_temp_receiver: a getter's reference off a temporary binds in
    # place, so the callee's write reaches what it lends
    grow(mk_gh().items)  # tpyc: ok
    print("property_temp_receiver", len(G))
    # property_temp_receiver_insert: the same read at a container insert,
    # which keeps nothing of a pointer-free list but its warned copy (the
    # copy is the stated divergence, so only the length is printed)
    rows: list[list[int32]] = []
    rows.append(mk_gh().items)  # tpyc: warning(/copies list\[int32\]/)
    print("property_temp_receiver_insert", len(rows))
    # generic_dict_literal: a dict literal at a generic callee's mutated
    # parameter is held for the call
    print("generic_dict_literal", take_dict(1, {"b": 2}))  # tpyc: ok
    # select_arg: a container select at an overloaded callee's parameter
    y = 0.5
    y = tot3({} if c else {})  # tpyc: ok
    print("select_arg", y)
    # own_bytearray: a new bytearray at an `Own[bytearray]` parameter
    print("own_bytearray", first_byte(bytearray(b"ab")))  # tpyc: ok
    print("narrowed_union_own", use_union(A(1)))
    # readonly_ctor_literal: a list literal at a readonly constructor parameter
    print("readonly_ctor_literal", Numbers([1, 2, 3]).total)  # tpyc: ok


class AgesIn:
    ages: dict[str, int32]

    def __init__(self) -> None:
        self.ages = {"a": 1}


class AgesOut:
    inner: AgesIn
    rows: list[dict[str, int32]]

    def __init__(self) -> None:
        self.inner = AgesIn()
        self.rows = [{"b": 2}]


class MaybeHeap:
    maybe: list[int32] | None

    def __init__(self) -> None:
        self.maybe = [3, 1, 2]

    def fix(self) -> int32:
        if self.maybe is not None:
            # narrowed_field_native: a narrowed Optional field at a native
            # callee's container parameter binds its container
            heapq.heapify(self.maybe)  # tpyc: ok
            return self.maybe[0]
        return -1


class Pod:
    x: int

    def __init__(self) -> None:
        self.x = 0


class PodHolder:
    o: Pod

    def __init__(self) -> None:
        self.o = Pod()


def poke_gen[U](o: U | None, f: Callable[[U], None]) -> bool:
    if o is not None:
        f(o)
    return o is not None


def inc_pod(p: Pod) -> None:
    p.x += 10


def more_reference_args() -> None:
    ah = AgesOut()
    d: dict[str, int32] = {}
    # field_chain: a field read through a nested receiver at `dict.update`
    d.update(ah.inner.ages)  # tpyc: ok
    # field_element: an element of a container field at `dict.update`
    d.update(ah.rows[0])  # tpyc: ok
    print("field_chain", len(d))
    print("narrowed_field_native", MaybeHeap().fix())
    # pointee_field_generic_opt: a field at a generic `U | None` parameter
    # the callee writes through; the caller sees the write
    t = PodHolder()
    print("pointee_field_generic_opt", poke_gen(t.o, inc_pod))  # tpyc: ok
    print("pointee_field_generic_opt_after", t.o.x)
    # or_select_arg: an `or` select of containers at an overloaded parameter
    y = 0.5
    y = tot3({} or {})  # tpyc: ok
    print("or_select_arg", y)
    # list_repeat_insert: a list repeat at a container element insert
    rows: list[list[float]] = []
    n = 3
    for _ in range(2):
        rows.append([0.0] * n)  # tpyc: ok
    print("list_repeat_insert", len(rows), len(rows[0]))


class RK:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self, a: A) -> int32:
        a.v += 100
        return self.n + a.v


def tick(n: list[int32]) -> int32:
    n[0] += 1
    return n[0]


def mk_rk(n: int32) -> Own[RK]:
    return RK(n)


def receiver_order() -> None:
    c = [0]
    # receiver_order: the argument held for a mutable `A&` parameter is
    # hoisted before the fresh receiver, so it ticks first (render pinned;
    # the order is BUGS.md#subexpression-right-to-left-eval's). The sum
    # is the same in either order
    r = mk_rk(tick(c)).bump(A(tick(c)))  # tpyc: ok
    print("receiver_order", r, c[0])


def opt_bump(o: A | None) -> int32:
    if o is None:
        return -1
    o.v += 1
    return o.v


def uni_bump(u: A | UB) -> int32:
    if isinstance(u, A):
        u.v += 2
        return u.v
    u.y += 2
    return u.y


class PtrSink:
    n: int32

    def __init__(self, o: A | None, u: A | UB) -> None:
        self.n = opt_bump(o)
        self.n += uni_bump(u)

    def opt(self, o: A | None) -> int32:
        return opt_bump(o)

    def uni(self, u: A | UB) -> int32:
        return uni_bump(u)


def pick_vu(f: bool) -> int32 | str:
    if f:
        return 1
    return "a"


def is_str_vu(v: int32 | str) -> bool:
    return isinstance(v, str)


class Pet:
    def __init__(self, n: int32) -> None:
        self.n = n


class Dog(Pet):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


def mk_dog() -> Own[Dog]:
    return Dog(4)


class PetProbe:
    def probe(self, p: Pet | None) -> int32:
        return -1 if p is None else p.n


class Crate[T]:
    def __init__(self, value: Own[T]) -> None:
        self.value = value


def wrap_crate[T](v: Own[T] | None) -> Own[Crate[T]] | None:
    if v is not None:
        return Crate(v)
    return None


def has_crate(c: Crate[int32] | None) -> bool:
    return c is not None


def pointer_args() -> None:
    # opt_field: a field at an `A | None` parameter lends its object, so the
    # callee's write shows in the holder
    h = mkh()
    print("opt_field", opt_bump(h.a), h.a.v)  # tpyc: ok
    h.a.v += 100
    print("opt_field_after", h.a.v)
    # uni_field: the same at an `A | UB` parameter
    print("uni_field", uni_bump(h.a), h.a.v)  # tpyc: ok
    # opt_method / uni_method: the method twins
    ps = PtrSink(None, UB(1))
    print("opt_method", ps.opt(h.a), ps.uni(h.a), h.a.v)  # tpyc: ok
    # ptr_ctor: the constructor twin
    print("ptr_ctor", PtrSink(h.a, h.a).n, h.a.v)  # tpyc: ok
    # value_union_native: a value union's call result at a native protocol
    # argument
    print("value_union_native", repr(pick_vu(True)))  # tpyc: ok
    # value_union_call: a value union's call result at a value-union parameter
    print("value_union_call", is_str_vu(pick_vu(True)), is_str_vu(pick_vu(False)))  # tpyc: ok
    # opt_subclass_call: a subclass object a call builds, at its base's
    # Optional parameter, is held as the subclass for the call
    print("opt_subclass_call", PetProbe().probe(mk_dog()))  # tpyc: ok
    # opt_own_call: an `Own[R] | None` call result at an `R | None` parameter
    print("opt_own_call", has_crate(wrap_crate(99)), has_crate(wrap_crate(None)))  # tpyc: ok


def make_ints(n: int32) -> Own[list[int32]]:
    return [n, n, n]


class Viewer:
    sp: Span[readonly[int32]]

    def __init__(self, sp: Span[readonly[int32]]) -> None:
        self.sp = sp


class FreshViewChild(Viewer):
    def __init__(self, n: int32) -> None:
        # base_init_fresh_view: a fresh list at the base's view parameter
        # converts as at a constructor call; the view dangles past the
        # initializer (BUGS.md#ctor-view-param-of-temporary-dangles), so it
        # is only built, never read
        super().__init__(make_ints(n))  # tpyc: ok


class TaggedError(Exception):
    def __init__(self, n: int32) -> None:
        # base_init_format_spec: an f-string with a format spec at the
        # message parameter
        super().__init__(f"tag{n:04d}")  # tpyc: ok


class BufBase:
    buf: bytearray

    def __init__(self, b: bytearray) -> None:
        self.buf = b  # tpyc: warning(/copies bytearray into field/)


class FreshBufChild(BufBase):
    def __init__(self) -> None:
        # base_init_fresh_const_ref: a fresh bytearray at a parameter the
        # base only reads binds for the initializer
        super().__init__(bytearray(b"xy"))  # tpyc: ok


def base_init_args() -> None:
    FreshViewChild(7)
    print("base_init_fresh_view", "built")
    try:
        raise TaggedError(5)
    except TaggedError as e:
        print("base_init_format_spec", str(e))
    c = FreshBufChild()
    c.buf.append(122)
    print("base_init_fresh_const_ref", len(c.buf), c.buf[2])


def wkeep(a: A) -> None:
    a.v += 10


def wkeep_own(a: Own[A]) -> int32:  # tpyc: warning(/param 'a' is never consumed/)
    a.v += 5
    return a.v


def wkeep_l(xs: list[int32]) -> None:
    xs.append(7)


class WSink:
    def __init__(self) -> None:
        pass

    def keep(self, a: A) -> None:
        a.v += 100


def walrus_args() -> None:
    # walrus_args: a walrus argument is read as its binding, which the
    # callee then writes; the write shows through both the target and the
    # source
    rc: Rc[A] = Rc.new(A(1))
    wkeep(p := deref(rc))  # tpyc: ok
    p.v += 1
    print("walrus_deref", p.v, deref(rc).v)
    h = A(1)
    wkeep(q := h)  # tpyc: ok
    q.v += 1
    print("walrus_name", q.v, h.v)
    wkeep(f := A(1))  # tpyc: ok
    f.v += 1
    print("walrus_fresh", f.v)
    ws = WSink()
    ws.keep(m := h)  # tpyc: ok
    m.v += 1
    print("walrus_method", m.v, h.v)
    ys = [1, 2]
    wkeep_l(zs := ys)  # tpyc: ok
    zs.append(8)
    print("walrus_list", len(ys), ys[3], zs[2])
    # walrus_own: an Own slot copies the target it is still read through, as
    # the plain binding's twin does; the warning pins the copy, so the output
    # reads the callee's object only (calls/argument_sink_warned_copy prints
    # the target too)
    r = wkeep_own(o := A(1))  # tpyc: warning(/copies A into owned storage/)
    print("walrus_own", r, o.v > 0)


def two_scalar(tag: str, x: Own[int32], y: int32) -> None:
    print(tag, x, y)


def scalar_order() -> None:
    n: int32 = 1

    def tick() -> int32:
        nonlocal n
        n += 10
        return n
    # scalar_order: an owned scalar's copy is read before a later argument
    # changes it, as CPython reads left to right
    two_scalar("scalar_order", n, tick())  # tpyc: ok
    print("scalar_order_after", n)


def make_ab(f: bool) -> Own[A | UB]:
    if f:
        return A(7)
    return UB(9)


def use_ab(x: A | UB) -> int32:
    if isinstance(x, A):
        x.v += 1
        return x.v
    return x.y


def some[T](x: T | None) -> bool:
    return x is not None


def wrap[T](xs: list[T]) -> bool:
    # open_optional: an open `T` element at an open `T | None` parameter,
    # at a record, a scalar and the view-family str / bytes instantiations
    return some(xs[0])  # tpyc: ok


def opt_view(o: str | None) -> bool:
    # opt_view: an Optional str view parameter at a generic `T | None`
    # parameter instantiated at `str` takes the view->owned copy
    return some(o)  # tpyc: ok


def opt_view_b(o: bytes | None) -> bool:
    return some(o)  # tpyc: ok


def opt_view_narrowed(o: str | None) -> bool:
    if o is not None:
        return some(o)  # tpyc: ok
    return some(o)


def vu_total(v: int32 | float) -> int32:
    return 2


def inline_narrowed(v: int32 | float) -> int32:
    # inline_narrowed: a ternary-narrowed value-union name at a value-union
    # parameter
    return vu_total(v) if isinstance(v, float) else -1  # tpyc: ok


gen_counter: int32 = 1


def gen_tick() -> int32:
    global gen_counter
    gen_counter += 10
    return gen_counter


def gen_args(h: H, s: str) -> Iterator[int32]:
    # gen_scalar_order: an owned scalar's copy is read before a later
    # argument changes it, inside a generator
    two_scalar("gen_scalar_order", gen_counter, gen_tick())  # tpyc: ok
    # gen_field: a field at a reference parameter inside a generator
    yield bump(h.a)  # tpyc: ok
    t = mkstr(1) + s
    # gen_own_str: an `Own[str]` local at its last use moves
    yield eats(t)  # tpyc: ok
    # gen_fresh: a fresh record at a mutable `A&` parameter is held
    yield bump(A(1))  # tpyc: ok


async def async_args(h: H) -> int32:
    await asyncio.sleep(0)
    # async_field: a field at a reference parameter after an await
    x = bump(h.a)  # tpyc: ok
    t = mkstr(2)
    # async_own_str: an `Own[str]` local at its last use moves
    y = eats(t)  # tpyc: ok
    # async_fresh: a fresh record at a mutable `A&` parameter is held
    z = bump(A(2))  # tpyc: ok
    await asyncio.sleep(0)
    return x + y + z


class Shape(Protocol):
    def area(self) -> int32: ...


@dynamic
class DynShape(Protocol):
    def area(self) -> int32: ...


class Sq:
    def __init__(self, k: int32) -> None:
        self.k = k

    def area(self) -> int32:
        self.k += 1
        return self.k * self.k


def measure(s: Shape) -> int32:
    return s.area()


def measure_dyn(s: DynShape) -> int32:
    return s.area()


def apply_fn(f: Callable[[A], int32], a: A) -> int32:
    return f(a)


class Gauge:
    _a: A

    def __init__(self) -> None:
        self._a = A(0)

    @property
    def a(self) -> A:
        return self._a

    @a.setter
    def a(self, v: A) -> None:
        v.v += 1
        self._a = v


class Container:
    @staticmethod
    def wrap_optional(v: int32) -> Own[A] | None:
        if v > 0:
            return A(v)
        return None


def check(a: A | None) -> int32:
    return -1 if a is None else a.v


def flag_f(a: A) -> bool:
    a.v += 1
    return a.v > 0


def flag_below(a: A, lim: int32) -> bool:
    a.v += 1
    return a.v <= lim


calls = [0]


def mk() -> Own[A]:
    calls[0] += 1
    return A(calls[0])


def key() -> int32:
    calls[0] += 1
    return 1


class Tally:
    n: int32
    last: int32

    def __init__(self) -> None:
        self.n = 0
        self.last = 0

    def __getitem__(self, k: A) -> int32:
        return self.n

    def __setitem__(self, k: A, v: int32) -> None:
        self.last = k.v
        self.n = v


class GetH:
    h: H

    def __init__(self) -> None:
        self.h = H(A(3))

    def geth(self) -> H:
        return self.h


def eat_box(b: Own[Box[A]]) -> int32:
    return b.get().v


def total_list(xs: readonly[list[int32]]) -> int32:
    return len(xs)


def str_or_int(u: str | int32) -> int32:
    return 1 if isinstance(u, str) else 2


def workarounds() -> None:
    # array_local_annotated: an annotated list local, and a literal written
    # at the call, are lists a `list` parameter binds
    bs: list[int32] = [1, 2]
    print("array_local_annotated", total_list(bs), total_list([1, 2]))  # tpyc: ok
    # literal_at_value_union: a str literal at `str | int32` builds its member
    print("literal_at_value_union", str_or_int("x"))  # tpyc: ok


def positions() -> None:
    scalar_order()
    # own_union_result: an owning union result at a union parameter is held
    # for the call and lent
    print("own_union_result", use_ab(make_ab(True)), use_ab(make_ab(False)))  # tpyc: ok
    # The str / bytes instantiations copy into an owned optional with no
    # warning (BUGS.md#generic-optional-param-owned-at-view-family).
    print("open_optional", wrap([A(1)]), wrap([7]), wrap(["s"]), wrap([b"b"]),
          some(None))
    print("opt_view", opt_view("a"), opt_view(None), opt_view_b(b"b"),
          opt_view_b(None), opt_view_narrowed("n"), opt_view_narrowed(None))
    print("inline_narrowed", inline_narrowed(3), inline_narrowed(1.5))
    h = mkh()
    gl = list(gen_args(h, "x"))
    print("gen", gl, h.a.v)
    print("async", asyncio.run(async_args(h)), h.a.v)
    sq = Sq(2)
    # protocol_slot / dyn_slot: a record at a protocol and a @dynamic
    # parameter stays on the protocol rows; the callee's write shows
    print("protocol_slot", measure(sq), sq.k)  # tpyc: ok
    print("dyn_slot", measure_dyn(sq), sq.k)  # tpyc: ok
    # callable_local: an argument through a lambda and a Callable local
    a = A(1)
    f: Callable[[A], int32] = lambda x: bump(x)
    print("callable_local", f(a), apply_fn(lambda x: bump(x), a), a.v)  # tpyc: ok
    # setter_value: a property setter's value is owned storage, which a
    # local at its last use moves into
    g = Gauge()
    b = A(5)
    g.a = b  # tpyc: ok
    print("setter_value", g.a.v)
    # static_own_optional: a static method's `Own[A] | None` result at an
    # `A | None` parameter
    print("static_own_optional", check(Container.wrap_optional(99)), check(Container.wrap_optional(0)))  # tpyc: ok


def once() -> None:
    flag = True
    # once_and: `flag and f(mk())` builds the argument once, only when read
    print("once_and", flag and flag_f(mk()), calls[0])  # tpyc: ok
    # once_while: the condition's argument is built once per test
    n = 0
    lim = calls[0] + 3
    while flag_below(mk(), lim):  # tpyc: ok
        n += 1
    print("once_while", n, calls[0])
    # once_elif: an `elif` condition's argument is built only when reached
    if calls[0] < 0:
        print("never")
    elif flag_f(mk()):  # tpyc: ok
        print("once_elif", calls[0])
    # once_comp_filter: a comprehension filter builds its argument per item
    xs = [i for i in range(3) if flag_f(mk())]  # tpyc: ok
    print("once_comp_filter", len(xs), calls[0])
    # once_aug_subscript: `d[key()] += 1` evaluates the key once
    d = {1: 5}
    d[key()] += 1  # tpyc: ok
    print("once_aug_subscript", d[1], calls[0])
    # once_aug_record_key: `v[mk()] += 1` builds the record key once
    v = Tally()
    v[mk()] += 1  # tpyc: ok
    print("once_aug_record_key", v.n, v.last, calls[0])
    # borrow_field_copy: a field off a borrow-returning call at an `Own`
    # parameter is the warned copy, never a move
    c = GetH()
    print("borrow_field_copy", eat_a(c.geth().a))  # tpyc: warning(/copies A into owned storage/)
    # nocopy_box_move: a `@nocopy` Box local at its last use moves
    bx = Box(A(4))
    print("nocopy_box_move", eat_box(bx))  # tpyc: ok


class Inc:
    def __call__(self, n: int32) -> int32:
        return n + 1


class ApplyS:
    def apply(self, f: Callable[[int32], int32]) -> int32:
        return f(2)


type RTree = int32 | str | A | list[RTree]


def tree_v(t: RTree) -> int32:
    if isinstance(t, A):
        return t.v
    if isinstance(t, str):
        return 1
    return 0


class TreeSink:
    # Forwards to a free function: a method body reading the union's
    # alternatives renders before the wrapper is complete (BUGS.md, the
    # non-generic recursive union by-value entry).
    def take(self, t: RTree) -> int32:
        return tree_v(t)


def method_slot_args() -> None:
    s = ApplyS()
    inc = Inc()
    # The Callable conversion copies the object; `Inc` is stateless, so the
    # copy is unobservable (BUGS.md#stateful-callable-store-copy-silent).
    print("method_callable_object", s.apply(inc))  # tpyc: ok
    # A recursive-union member at a recursive-union method parameter is
    # copied into the wrapper temp; the callee reads it only.
    ts = TreeSink()
    a = A(7)
    print("method_ru_member_name", ts.take(a))  # tpyc: ok
    print("method_ru_member_rvalue", ts.take(A(8)))  # tpyc: ok
    print("method_ru_literal", ts.take("hi"))  # tpyc: ok



class OwnKeyed:
    d: dict[str, int32]

    def __init__(self) -> None:
        self.d = {}

    def __getitem__(self, k: Own[str]) -> int32:
        return self.d.get(k, 0)

    def __setitem__(self, k: Own[str], v: int32) -> None:
        self.d[k] = v


class RoKeyed:
    def __getitem__(self, k: readonly[A]) -> int32:
        return k.v * 10


def aug_key() -> None:
    # record_key_readonly: a fresh record key at a `readonly` key parameter
    # binds for the call, as the explicit `__getitem__` call's argument does
    rk = RoKeyed()
    print("record_key_readonly", rk[A(2)], rk[mk()])  # tpyc: ok
    t = OwnKeyed()
    s = mkstr(1)
    # aug_key_own_str: an augmented subscript's key is read by the getter and
    # the setter, so neither moves it
    t[s] += 1  # tpyc: ok
    print("aug_key_own_str", t.d)


def main() -> None:
    # field at a reference parameter: the callee's write shows in the source
    hl = mkh()
    ref(hl.a)  # tpyc: ok
    print("ref_field", hl.a.v)
    # the held copy runs before its sibling argument (render pinned; the
    # order itself is BUGS.md#subexpression-right-to-left-eval's)
    a = A(1)
    two(a, bump(a))  # tpyc: warning(/copies A into owned storage/)
    two(copy(a), bump(a))  # tpyc: ok
    print("order", a.v)
    # free call: Own[str] at its last use moves; read again in its
    # statement it does not
    v = mkstr(2)
    os_(v)  # tpyc: ok
    s = mkstr(1)
    print("own_str_stmt", ns(s), eats(s))  # tpyc: ok
    # method: a live Own[str] name is copied, then moved at its last use
    e = E()
    m = mkstr(3)
    print("method_copy", e.take(m))  # tpyc: ok
    print("method_move", e.take(m))  # tpyc: ok
    # constructor: the same at an Own[str] parameter
    c = mkstr(1)
    k1 = K(c)  # tpyc: ok
    k2 = K(c)  # tpyc: ok
    print("ctor", k1.x, k2.x)
    # own_enum: an enum member at a generic constructor's `Own[T]` slot
    p = Keep(Color.RED)  # tpyc: ok
    print("own_enum", p.first == Color.BLUE, p.first == Color.RED)
    # generic_borrow: a borrow-returning call at a generic free function's
    # `T` slot binds the holder's field, so the callee's write shows there
    h = Holder()
    poke(h.borrow())  # tpyc: ok
    print("generic_borrow", h.c.n)
    o = Outer[int32](1)
    o.put_own(9)
    print("own_tparam_fwd", o._in.v)
    view_set_add(b"ab")
    # own_opt_call: an `Own[Point] | None` call result at an argument slot
    ph = PHolder()
    put(ph, maybe_make(3))  # tpyc: ok
    pv = ph.value
    assert pv is not None
    pv.x += 1
    pw = ph.value
    assert pw is not None
    print("own_opt_call", pw.x)
    narrowed(100)
    # own_scalar_field: an `int` field read at an `Own[int32]` slot
    bg = Big()
    print("own_scalar_field", take(bg.big))  # tpyc: ok
    b = Bag[A]()
    src = [A(1)]
    b.echo(src, 0)
    print("own_elem", len(b.held), b.held[0].v)
    CBox[3]().run()
    total = feed(Sink(), b"ab")
    print("own_bytes_total", total)
    # value_opt_field: a value record field at an `Optional` value slot
    vh = VH()
    w = W()
    print("value_opt_field", vh.m(w.v))  # tpyc: ok
    reference_args(True)
    more_reference_args()
    receiver_order()
    pointer_args()
    base_init_args()
    walrus_args()
    positions()
    once()
    workarounds()
    aug_key()
    method_slot_args()

main()
