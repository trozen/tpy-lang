# A field write takes every source its slot's value form admits, at every
# position (warned copies live in records/field_admission_warned).
import asyncio
from typing import Any, Callable, Iterator, Optional
from tpy import Own, StrView, ValueType, copy, float64, int32, int64, nocopy


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Inner:
    tag: Optional[str]

    def __init__(self, tag: Optional[str]) -> None:
        self.tag = tag


class Box:
    tag: Optional[str]
    raw: Optional[bytes]
    either: int32 | str
    pair: tuple[int, str]
    xs: list[int32]
    p: P
    op: Optional[P]
    n64: Optional[int64]

    def __init__(self, e: int32 | str) -> None:
        self.tag = None
        self.raw = None
        self.either = e
        self.pair = (0, "")
        self.xs = []
        self.p = P(0)
        self.op = None
        self.n64 = None

    def show(self, label: str) -> None:
        print(label, self.tag, self.raw, self.either, self.pair, self.xs,
              self.p.v, self.op is None, self.n64)

    def method_writes(self, s: Optional[str], b: Optional[bytes], text: str,
                      n: int32, wide: int64, pairs: list[tuple[int, str]],
                      ps: list[P], inner: Inner, flag: bool) -> None:
        # method: an `Optional[str]` / `Optional[bytes]` param lands through
        # the view->owned shim; str / bytes values through their owned twin.
        self.tag = s  # tpyc: ok
        self.raw = b  # tpyc: ok
        self.show("method.opt_params")
        self.tag = text + "!"  # tpyc: ok
        self.raw = b"xy"  # tpyc: ok
        self.show("method.opt_fresh")
        self.tag = text[1:3]  # tpyc: ok
        self.tag = inner.tag  # tpyc: ok
        self.show("method.opt_view_field")
        # method: a value union takes a literal, a view member, a conversion.
        self.either = 7  # tpyc: ok
        self.show("method.union_literal")
        self.either = text  # tpyc: ok
        self.show("method.union_view")
        self.either = str(n)  # tpyc: ok
        self.show("method.union_conv")
        # method: a value tuple from a container element; an Optional
        # int64 from a fixed-width value.
        self.pair = pairs[0]  # tpyc: ok
        self.n64 = wide  # tpyc: ok
        self.show("method.tuple_int64")
        # method: a fresh list and a fresh select of records own their
        # storage -- mutating the field reaches nothing else.
        self.xs = [n] + [2]  # tpyc: ok
        self.xs.append(9)
        self.p = P(1) if flag else P(2)  # tpyc: ok
        self.p.v = self.p.v + 10
        # method: an explicit copy of an element into an Optional record
        # slot; the source element is mutated after the write.
        self.op = copy(ps[0])  # tpyc: ok
        ps[0].v = 99
        self.show("method.ref_fresh")
        op = self.op
        if op is not None:
            print("method.op_copy", op.v, ps[0].v)

    def gen_writes(self, s: Optional[str], text: str,
                   n: int32) -> Iterator[int32]:
        # generator method: the same writes inside a resumable frame.
        self.tag = s  # tpyc: ok
        yield 1
        self.either = text  # tpyc: ok
        self.either = 11  # tpyc: ok
        self.xs = [n] + [3]  # tpyc: ok
        self.xs.append(8)
        self.show("gen.writes")
        # generator method: a container literal whose element hoists an
        # argument temp before the write.
        self.xs = [count([n, n]), 7]  # tpyc: ok
        print("gen.literal_arg_temp", self.xs)
        # generator method: a frame-local list at its last use moves in; the
        # field is then the only owner.
        ys = [n, 30]
        ys.append(31)
        self.xs = ys  # tpyc: ok
        self.xs.append(32)
        # generator method: a frame-local str read again after the write
        # stays intact.
        label = text + "!"
        self.tag = label  # tpyc: ok
        yield 2
        print("gen.frame_locals", self.xs, self.tag, label)

    async def async_writes(self, s: Optional[str], text: str,
                           n: int32) -> None:
        # async method: the same writes across a suspension.
        await asyncio.sleep(0)
        self.tag = s  # tpyc: ok
        self.either = str(n)  # tpyc: ok
        await asyncio.sleep(0)
        self.pair = (n, text)  # tpyc: ok
        self.show("async.writes")
        # async method: a frame-local list moves in at its last use; a
        # frame-local str read after the write stays intact.
        ys = [n, 40]
        ys.append(41)
        label = text + "?"
        self.xs = ys  # tpyc: ok
        self.tag = label  # tpyc: ok
        await asyncio.sleep(0)
        self.xs.append(42)
        print("async.frame_locals", self.xs, self.tag, label)


class Flat:
    n: int32

    def __init__(self, data: list[bytes]) -> None:
        self.n = len(data)


class Keep:
    ba: bytearray | None
    flat: Flat

    def __init__(self) -> None:
        self.ba = None
        self.flat = Flat([b"a"])

    def load(self, src: bytes) -> None:
        # method: a converting construction into an Optional reference slot.
        self.ba = bytearray(src)  # tpyc: ok
        # method: a constructor whose argument hoists a temp before the
        # write statement.
        self.flat = Flat([bytes([i]) for i in range(5)])  # tpyc: ok
        print("method.adjacent", self.ba, self.flat.n)


class Vals:
    t: tuple[int, int32]
    o: int | None
    s: str

    def __init__(self) -> None:
        self.t = (0, 0)
        self.o = None
        self.s = ""

    def keep(self, t: tuple[int, int32], o: int | None, s: str) -> None:
        # method: by-value tuple / Optional / str params written and then
        # read again -- the write must not move them.
        self.t = t  # tpyc: ok
        self.o = o  # tpyc: ok
        self.s = s  # tpyc: ok
        print("method.not_last_use", t, o, s, self.t, self.o, self.s)


def holder_writes(s: Optional[str], text: str, n: int32,
                  pairs: list[tuple[int, str]]) -> None:
    # local holder: a free function writing fields of a local instance.
    h = Box(0)
    h.tag = s  # tpyc: ok
    h.either = text  # tpyc: ok
    h.pair = pairs[0]  # tpyc: ok
    xs = [n, 4]
    xs.append(5)
    h.xs = xs  # tpyc: ok
    h.xs.append(6)
    # local holder: a whole-Optional container element.
    wides: list[Optional[int64]] = [77]
    h.n64 = wides[0]  # tpyc: ok
    h.show("holder.writes")
    # local holder: a constructor whose argument hoists a temp.
    k = Keep()
    k.flat = Flat([bytes([i]) for i in range(4)])  # tpyc: ok
    print("holder.ctor_arg_temp", k.flat.n)


class Q:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


class Src:
    cb: Callable[[int32], None]
    v: P

    def __init__(self, cb: Callable[[int32], None], v: Own[P]) -> None:
        self.cb = cb
        self.v = v

    def peek(self) -> P:
        return self.v


@nocopy
class Res:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def __del__(self) -> None:
        pass


def make_buf(n: int32) -> Own[bytearray]:
    return bytearray(n)


def make_list(n: int32) -> Own[list[int32]]:
    return [n, n + 1]


def make_res(v: int32) -> Own[Res]:
    return Res(v)


def show_cb(n: int32) -> None:
    print("ctor.callback", n)


def pick(a: int32) -> int32:
    print("ctor.pick", a)
    return a


g_limit: int32 = 5


class Built:
    buf: bytearray
    xs: list[int32]
    cp: P
    cb: Callable[[int32], None]
    n: int32
    action: Callable[[], None]
    payload: Any
    name: str
    lab: StrView
    pair: tuple[int32, P]
    other: P
    lim: int32
    lim2: int32
    res: Res
    flat: Flat
    late: int32

    def __init__(self, n: int32, s: Src, p: Any, text: str,
                 own: Own[P], flag: bool) -> None:
        # ctor: free calls returning an owned reference value.
        self.buf = make_buf(n)  # tpyc: ok
        self.xs = make_list(n)  # tpyc: ok
        # ctor: an explicit copy of a borrow-returning call.
        self.cp = copy(s.peek())  # tpyc: ok
        # ctor: a callable read off another record's field.
        self.cb = s.cb  # tpyc: ok
        self.n = n
        # ctor: a lambda that captures no `self`.
        self.action = lambda: print("ctor.lambda", n)  # tpyc: ok
        # ctor: an already-Any source.
        self.payload = p  # tpyc: ok
        # ctor: an explicit copy of a str view into an owned str.
        self.name = copy(text)  # tpyc: ok
        # ctor: a view field from a slice of a view param.
        self.lab = text[1:3]  # tpyc: ok
        # ctor: copy() of an Own param in a tuple, then its last use moves
        # it into another field.
        self.pair = (1, copy(own))  # tpyc: ok
        self.other = own  # tpyc: ok
        # ctor: global reads, bare and inside an expression.
        self.lim = g_limit  # tpyc: ok
        self.lim2 = g_limit + 1  # tpyc: ok
        # ctor: a non-default-constructible field from a conditional of
        # owned calls.
        self.res = make_res(1) if flag else make_res(2)  # tpyc: ok
        # ctor: a source that needs a statement temporary is assigned in
        # the body instead.
        self.flat = Flat([bytes([i]) for i in range(3)])  # tpyc: ok
        k = n * 2
        # ctor: a source reading a body local is assigned in the body.
        self.late = k  # tpyc: ok

    def show(self) -> None:
        self.cb(self.n)
        self.action()
        self.xs.append(99)
        print("ctor.fields", len(self.buf), self.xs, self.cp.v, self.name,
              self.lab, self.pair[0], self.pair[1].v, self.other.v,
              self.lim, self.lim2, self.res.val, self.flat.n, self.late)


def count(xs: list[int32]) -> int32:
    return len(xs)


def mk(xs: list[int32]) -> Own[list[int32]]:
    return [xs[0], 9]


class UA:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class UB:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def union_tag(x: UA | UB) -> int32:
    return x.v if isinstance(x, UA) else -x.v


class Placed:
    ks: list[int32]
    ys: list[int32]
    n: int32
    n2: int32
    t: tuple[int32, int32]
    zs: list[int32]
    u: int32
    xs: list[int32]

    def __init__(self, n: int32, a: int32, src: list[int32]) -> None:
        # ctor: a comprehension whose element hoists an argument temp keeps
        # it inside its own loop body, so the init stays a member-init.
        self.ks = [count([i]) for i in range(n)]  # tpyc: ok
        # ctor: a comprehension whose SOURCE hoists an argument temp needs
        # the statement before it -- the init becomes a body assignment.
        self.ys = [x for x in mk([1, 2])]  # tpyc: ok
        # ctor: an argument temp, bare and under a call-shaped argument, and
        # a tuple literal holding an `or` operand -- all body assignments.
        self.n = count([1, 2])  # tpyc: ok
        self.n2 = count(mk([1]))  # tpyc: ok
        self.t = (pick(a) or 1, 2)  # tpyc: ok
        # ctor: a select whose fresh left operand is held once, and a record
        # rvalue lifted into a union parameter -- both body assignments.
        self.zs = mk(src) or [1]  # tpyc: ok
        self.u = union_tag(UA(a + 4))  # tpyc: ok
        self.xs = []

    def fill(self) -> None:
        # method: a container literal whose element hoists an argument temp
        # before the write statement.
        self.xs = [count([1, 2]), 3]  # tpyc: ok
        print("method.literal_arg_temp", self.xs)

    def show(self) -> None:
        print("ctor.placed", self.ks, self.ys, self.n, self.n2, self.t,
              self.zs, self.u)


class Pt(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __str__(self) -> str:
        return "Pt(" + str(self.x) + ")"


class Closed:
    pt: Pt | None
    u: Pt | int32
    r: tuple[float64, int32] | None
    v: int32
    s: str | None
    bs: bytes | None
    b2: bytes | None
    b3: bytes | None

    def __init__(self, pt: Pt | None, k: int32, a: int32, t: str,
                 tb: bytes, c: bool) -> None:
        # ctor: a ValueType record in an Optional field, from its param.
        self.pt = pt  # tpyc: ok
        # ctor: a ValueType constructor call into a value-union field.
        self.u = Pt(k)  # tpyc: ok
        # ctor: a tuple literal into an Optional value-tuple field.
        self.r = (1.5, 3)  # tpyc: ok
        # ctor: an `or` whose left operand is held once in a temporary --
        # the init becomes a body assignment.
        self.v = pick(a) or pick(1)  # tpyc: ok
        # ctor: str / bytes params and a bytes literal / select into
        # Optional str / bytes fields.
        self.s = t  # tpyc: ok
        self.bs = tb  # tpyc: ok
        self.b2 = b"z"  # tpyc: ok
        self.b3 = b"z" if c else None  # tpyc: ok

    def show(self) -> None:
        pt = self.pt
        if pt is not None:
            print("ctor.closed_pt", pt.x)
        print("ctor.closed_union", self.u)
        print("ctor.closed", self.r, self.v, self.s, self.bs, self.b2, self.b3)


class Base:
    z: int32

    def __init__(self) -> None:
        self.z = 0


class Derived(Base):
    b: str
    a: str
    d: int32
    c: int32

    def __init__(self, s: Own[str]) -> None:
        super().__init__()
        # ctor with a base: own fields are laid out in assignment order, so
        # the list runs in source order -- the first read copies, the last
        # one moves.
        self.a = s  # tpyc: warning(/copies str into field/)
        self.b = s  # tpyc: ok
        # ... and their side effects run in source order too.
        self.c = pick(3)  # tpyc: ok
        self.d = pick(4)  # tpyc: ok


class Q0:
    v: int32

    def __init__(self) -> None:
        print("ctor.based_q0_init")
        self.v = 1


class BasedOrder(Base):
    b: Q0
    r: Res
    a: int32

    def __init__(self, n: int32) -> None:
        super().__init__()
        # ctor with a base, fields declared out of assignment order: each
        # field is built once, in source order -- a default-constructible
        # record and a non-default-constructible one alike.
        self.a = n  # tpyc: ok
        self.b = Q0()  # tpyc: ok
        self.r = make_res(n)  # tpyc: ok


class ViewSlots:
    o: StrView | None
    u: StrView | int32

    def __init__(self, s: StrView) -> None:
        # ctor: a view-holding Optional slot filled with None, and a union's
        # view member direct-initialized from a view param.
        self.o = None  # tpyc: ok
        self.u = s  # tpyc: ok

    def set(self, k: int32) -> None:
        # method: the same, from a param.
        self.u = k  # tpyc: ok
        self.o = None  # tpyc: ok


class Tagged:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Holds:
    t: Tagged

    def __init__(self) -> None:
        self.t = Tagged("held")

    def peek(self) -> Tagged:
        return self.t


def view_of_borrow() -> None:
    # A view of a field read through a borrow-returning call lives as long
    # as the receiver.
    h = Holds()
    v: StrView = h.peek().name  # tpyc: ok
    print("local.view_of_borrow", v)


class Reads(Base):
    b: int32
    a: int32

    def __init__(self) -> None:
        super().__init__()
        self.a = 5
        # ctor with a base: own fields take assignment order, so `b`'s
        # member init runs after `a`'s and reads its value.
        self.b = self.a + 1  # tpyc: ok


class Slot:
    x: str

    def __init__(self) -> None:
        self.x = ""


def nested_writes(p: Own[str], t: tuple[int, int32] | None,
                  n: int | None) -> None:
    # nested def: a captured Own param written to a field is never moved --
    # the closure may run again.
    h = Slot()
    v = Vals()

    def inner() -> None:
        h.x = p  # tpyc: warning(/copies str into field/)
        # ... nor a captured by-value param.
        v.t = t if t is not None else (0, 0)  # tpyc: ok
        v.o = n  # tpyc: ok
    inner()
    inner()
    print("nested.captured", len(h.x), len(p), v.t, v.o, t, n)


class Sink:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = []

    def put(self, xs: Own[list[int32]]) -> int32:
        self.xs = xs
        return len(self.xs)


def call_lambda(f: Callable[[], int32]) -> int32:
    return f()


def lambda_reads() -> None:
    # A lambda built after the call still reads `xs`, so the call copies
    # it instead of moving it.
    xs = [1, 2, 3]
    k = Sink()
    n = k.put(xs)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("lambda.live", n, call_lambda(lambda: len(xs)))


def ctor_writes() -> None:
    s = Src(show_cb, P(3))
    built = Built(4, s, 1, "abcd", P(8), True)
    # The Own param's field is its own storage.
    built.other.v = built.other.v + 1
    built.show()
    closed = Closed(Pt(2), 6, 0, "tt", b"bb", False)
    closed.show()
    pl = Placed(3, 0, [5])
    pl.show()
    pl.fill()
    d = Derived("a string long enough to leave the small buffer")
    print("ctor.base", d.a == d.b, len(d.a), d.c, d.d)
    r = Reads()
    print("ctor.base_reads", r.a, r.b)
    bo = BasedOrder(7)
    print("ctor.based_order", bo.a, bo.b.v, bo.r.val)
    vs = ViewSlots("vs")
    vs.set(3)
    print("view_slots", vs.o is None)
    view_of_borrow()


async def main_coro(b: Box) -> None:
    await b.async_writes("async", "tx", 3)


def main() -> None:
    b = Box("init")
    b.show("start")
    b.method_writes("s", b"b", "text", 42, 5000000000, [(1, "one")],
                    [P(5)], Inner("in"), True)
    b.method_writes(None, None, "abc", 7, 1, [(2, "two")], [P(6)],
                    Inner(None), False)
    for k in b.gen_writes("gen", "gt", 8):
        print("gen.yield", k)
    asyncio.run(main_coro(b))
    holder_writes("hold", "ht", 9, [(3, "three")])
    k = Keep()
    k.load(b"src")
    vals = Vals()
    vals.keep((10**20, 7), 10**21, "kept string long enough to heap")
    nested_writes("a captured string long enough to heap", (10**20, 2),
                  10**30)
    lambda_reads()
    ctor_writes()


main()
