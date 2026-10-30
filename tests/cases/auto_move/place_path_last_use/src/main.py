# A field / tuple-index path read at an owning sink moves iff its root dies
# there; every other read is the warned copy (moving sections use `@nocopy`).
import asyncio
from typing import Callable, Iterator, Self
from tpy import Own, Ptr, ReturnException, error_return, int32, nocopy, nomove


@nocopy
class NB:
    def __init__(self, n: int32) -> None:
        self.n = n


@nomove
class M:
    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    def __init__(self, w: int32) -> None:
        self.w = w


class A:
    t: str

    def __init__(self, v: int32, b: Own[B]) -> None:
        self.v = v
        self.b = b
        self.t = "t" + str(v)

    def bump(self) -> None:
        self.v += 1


class H:
    def __init__(self, a: Own[A], a2: Own[A], s: str, l: Own[list[int32]],
                 box: Own[NB], o: Own[A | None]) -> None:
        self.a = a
        self.a2 = a2
        self.s = s
        self.l = l
        self.box = box
        self.o = o

    @property
    def pa(self) -> A:
        return self.a


class HC:
    def __init__(self, a: Own[A], a2: Own[A], s: str, l: Own[list[int32]],
                 o: Own[A | None]) -> None:
        self.a = a
        self.a2 = a2
        self.s = s
        self.l = l
        self.o = o

    @property
    def pa(self) -> A:
        return self.a

    def gen(self, label: str) -> Iterator[int32]:
        try:
            yield 1
        finally:
            print(label, "finally", len(self.l))


class LD:
    xs: list[int32]

    def __init__(self, s: str) -> None:
        self.xs = [1, 2, 3]
        self.s = s

    def __del__(self) -> None:
        print("LD.__del__", self.s)


def mkld(s: str) -> Own[LD]:
    return LD(s)


def grow(xs: list[int32]) -> int32:
    xs.append(4)
    return len(xs)


def keep_l(xs: Own[list[int32]]) -> int32:
    k = KL()
    k.keep(xs)
    return len(k.lz[0])


class HD:
    def __init__(self, a: Own[A]) -> None:
        self.a = a

    def __del__(self) -> None:
        print("HD.__del__", self.a.t)


class HDChild(HD):
    def __init__(self, a: Own[A]) -> None:
        super().__init__(a)


class HW:
    def __init__(self, a: Own[A]) -> None:
        self.a = a

    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        print("named_with exit", self.a.t)


class HHD:
    def __init__(self, hd: Own[HD]) -> None:
        self.hd = hd


class LA:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]


class HN:
    def __init__(self, a: Own[LA]) -> None:
        self.a = a

    def __del__(self) -> None:
        print("nested_del finalizer", len(self.a.xs))


class I:
    def __init__(self, nb: Own[NB]) -> None:
        self.nb = nb


class Nest:
    def __init__(self, i: Own[I]) -> None:
        self.i = i

    def take(self: Own[Self]) -> Own[NB]:
        # a consuming method's nested field `self.i.nb`
        return self.i.nb  # tpyc: ok


class SB:
    s: str
    b: bytes

    def __init__(self) -> None:
        self.s = "sb"
        self.b = b"sb"


class KL:
    lz: list[list[int32]]

    def __init__(self) -> None:
        self.lz = []

    def keep(self, xs: Own[list[int32]]) -> None:
        self.lz.append(xs)


class HM:
    m: M

    def __init__(self, n: int32) -> None:
        self.m = M(n)


class Slot:
    nb: NB

    def __init__(self) -> None:
        self.nb = NB(0)

    def put(self) -> None:
        h = mkh(5)
        self.nb = h.box  # tpyc: ok
        print("self_store", self.nb.n)


class Slot2:
    a: A

    def __init__(self) -> None:
        self.a = mka(0)


class Keeper:
    def __init__(self, nb: Own[NB]) -> None:
        self.nb = nb

    def eat(self, nb: Own[NB]) -> None:
        print("method", nb.n)


class Pair:
    def __init__(self, box: Own[NB]) -> None:
        self.box = box

    def take(self: Own[Self]) -> int32:
        # a consuming method's own field at its return's `Own` parameter
        return eat_nb(self.box)  # tpyc: ok


class Fail(Exception, ReturnException):
    pass


def mka(n: int32) -> Own[A]:
    return A(n, B(n * 10))


def mkh(n: int32) -> Own[H]:
    return H(mka(n), mka(n + 1), "s" + str(n), [n], NB(n), mka(n + 2))


def mkc(n: int32) -> Own[HC]:
    return HC(mka(n), mka(n + 1), "s" + str(n), [n], mka(n + 2))


def eat_a(a: Own[A]) -> None:
    kept = [a]
    kept[0].v += 100
    print("eat_a", kept[0].v)


def eat_b(b: Own[B]) -> None:
    kept = [b]
    print("eat_b", kept[0].w)


def eat_nb(nb: Own[NB]) -> int32:
    return nb.n


def eat_s(s: Own[str]) -> None:
    print("eat_s", s)


def eat_l(xs: Own[list[int32]]) -> None:
    k = KL()
    k.keep(xs)
    k.lz[0].append(9)
    print("eat_l", len(k.lz[0]))


def eat_l2(xs: Own[list[int32]], r: list[int32]) -> None:
    k = KL()
    k.keep(xs)
    print("eat_l2", len(k.lz[0]), len(r))


def sink_a(a: Own[A]) -> int32:
    kept = [a]
    return kept[0].b.w


def two(a: Own[A], h: HC) -> None:
    kept = [a]
    print("two", kept[0].b.w, h.s)


def two_a(a: Own[A], b: Own[A]) -> None:
    kept = [a, b]
    print("two_a", kept[0].b.w, kept[1].b.w)


def two_h(h: Own[HC], r: list[int32]) -> None:
    kept = [h]
    print("two_h", len(r), kept[0].s)


def keep_hc(h: Own[HC]) -> int32:
    kept = [h]
    return len(kept[0].l)


def mkt() -> Own[tuple[A, int32]]:
    return (mka(60), 2)


async def read_l(xs: list[int32]) -> int32:
    await asyncio.sleep(0)
    return len(xs)


async def read_a(a: A) -> int32:
    await asyncio.sleep(0)
    return a.b.w


async def work(xs: list[int32]) -> int32:
    await asyncio.sleep(0)
    return len(xs)


# --- moves ---------------------------------------------------------------


def free_fn() -> None:
    h = mkh(1)
    # free function: an `Own` parameter
    eat_a(h.a)  # tpyc: ok


def method() -> None:
    h = mkh(2)
    k = Keeper(NB(0))
    # method: an `Own` parameter
    k.eat(h.box)  # tpyc: ok


def ctor() -> None:
    h = mkh(3)
    # constructor: an `Own` parameter
    k = Keeper(h.box)  # tpyc: ok
    print("ctor", k.nb.n)


def field_store(o: Slot) -> None:
    h = mkh(4)
    # field store
    o.nb = h.box  # tpyc: ok
    print("field_store", o.nb.n)


def append() -> None:
    h = mkh(6)
    xs: list[NB] = []
    # container insert
    xs.append(h.box)  # tpyc: ok
    print("append", xs[0].n)


def return_own() -> Own[NB]:
    h = mkh(7)
    # return at `-> Own[NB]`
    return h.box  # tpyc: ok


def tuple_member() -> Own[tuple[NB, int32]]:
    h = mkh(8)
    # tuple-literal member at an owned tuple slot
    return (h.box, 8)  # tpyc: ok


def nested() -> None:
    h = mkh(9)
    # nested field path h.a.b
    eat_b(h.a.b)  # tpyc: ok


def element_field() -> None:
    t = (mkh(10), 1)
    print("element_field", t[1])
    # a field of a tuple element: t[0].box
    print("element_field", eat_nb(t[0].box))  # tpyc: ok


def tuple_element_store(o: Slot) -> None:
    t = (NB(12), 1)
    # a tuple element at a field store
    o.nb = t[0]  # tpyc: ok
    print("tuple_element_store", o.nb.n)


def str_field() -> None:
    h = mkh(13)
    # a str field at `Own[str]` moves its buffer (the callee keeps it)
    eat_s(h.s)  # tpyc: ok


def list_field() -> None:
    h = mkh(14)
    # a list field
    eat_l(h.l)  # tpyc: ok


def own_param_root(h: Own[H]) -> None:
    # an `Own[H]` parameter's field: moving it consumes `h`
    print("own_param_root", eat_nb(h.box))  # tpyc: ok


def gen() -> Iterator[int32]:
    h = mkh(16)
    yield 1
    # generator body, the owner held across a yield
    yield eat_nb(h.box)  # tpyc: ok


async def co() -> None:
    h = mkh(17)
    await asyncio.sleep(0)
    # async body, the owner held across an await
    print("async", eat_nb(h.box))  # tpyc: ok


@error_return(Fail)
def er(c: bool) -> int32:
    h = mkh(18)
    if c:
        raise Fail()
    # @error_return body
    return eat_nb(h.box)  # tpyc: ok


def match_arm(k: int32) -> None:
    h = mkh(19)
    match k:
        case 1:
            # match arm
            print("match_arm", eat_nb(h.box))  # tpyc: ok
        case _:
            print("match_arm other")


def consuming() -> None:
    # a consuming method's own field (the `return self.box` above)
    print("consuming", Pair(NB(20)).take())


def consuming_nested() -> None:
    print("consuming_nested", Nest(I(NB(21))).take().n)


def value_store(o: SB) -> None:
    h = mkh(22)
    h2 = SB()
    # a str and a bytes field at a field store
    o.s = h.s  # tpyc: ok
    o.b = h2.b  # tpyc: ok
    print("value_store", o.s, len(o.b))


def value_member() -> tuple[str, int32]:
    h = mkh(23)
    # a str field as a returned tuple member
    return (h.s, 23)  # tpyc: ok


def list_member() -> Own[tuple[list[int32], int32]]:
    h = mkh(24)
    # a list field as a returned tuple member
    return (h.l, 24)  # tpyc: ok


def list_element() -> Own[list[list[int32]]]:
    h = mkh(25)
    # a list field as a returned list element
    return [h.l]  # tpyc: ok


def temp_del() -> None:
    # a member of a temporary owner with `__del__`: the finalizer reads it
    n = sink_a(HD(mka(26)).a)  # tpyc: warning(/copies A into owned storage/)
    print("temp_del", n)


def temp_del_reads() -> None:
    # a member of a temporary owner with `__del__` read in place: a method
    # call and a borrowed parameter on it copy nothing
    HD(mka(27)).a.bump()  # tpyc: ok
    mkld("temp_del_reads_list").xs.append(4)  # tpyc: ok
    print("temp_del_reads", grow(mkld("temp_del_reads_grow").xs))  # tpyc: ok
    # a local bound to one copies nothing either: it takes the member as
    # the temporary hands it over (BUGS.md#temporary-finalized-owner-member-moved)
    x = mkld("temp_del_reads_local").xs  # tpyc: ok
    print("temp_del_reads", len(x))


# --- kept copies -----------------------------------------------------------


def read_later() -> None:
    h = mkc(30)
    # the root is read later
    print("read_later", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    print("read_later", h.s)


def aliased() -> None:
    h = mkc(31)
    r = h
    # the root is aliased
    print("aliased", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    print("aliased", r.s)


def live_sub_borrow() -> None:
    h = mkc(32)
    x = h.a
    # a borrow of the path is still live
    print("live_sub_borrow", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    print("live_sub_borrow", x.b.w)


def same_stmt_root() -> None:
    h = mkc(33)
    # the root is read again in the same statement
    two(h.a, h)  # tpyc: warning(/copies A into owned storage/)


def same_stmt_paths() -> None:
    h = mkc(34)
    # two paths of one root in one statement
    two_a(h.a, h.a2)  # tpyc: warning(/copies A into owned storage/)


def loop() -> None:
    h = mkc(35)
    for i in range(2):
        # a loop body
        print("loop", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)


def closure() -> None:
    h = mkc(36)
    f: Callable[[], str] = lambda: h.s  # tpyc: warning(/lambda at line \d+ copies the object 'h'/)
    # a closure captures the root
    print("closure", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    print("closure", f())


def finally_reads() -> None:
    h = mkc(37)
    try:
        # a `finally` reads the root
        print("finally", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    finally:
        print("finally", h.s)


def except_reads() -> None:
    h = mkc(38)
    try:
        # an `except` handler reads the root
        print("except", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    except ValueError:
        print("except", h.s)


def comprehension() -> None:
    h = mkc(39)
    # a comprehension element runs once per item
    print("comprehension", [sink_a(h.a) for _ in range(1)])  # tpyc: warning(/copies A into owned storage/)


async def pending_coroutine() -> None:
    h = mkc(40)
    c = read_a(h.a)
    # a pending coroutine borrows the path
    print("pending_coroutine", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)
    print("pending_coroutine", await c)


def owner_del() -> None:
    h = HD(mka(41))
    # the owner defines `__del__`, which reads the field
    print("owner_del", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)


def nomove_leaf(o: HM) -> None:
    h = HM(42)
    # a `@nomove` leaf cannot move
    o.m = h.m  # tpyc: warning(/copies M into field/)
    print("nomove_leaf", o.m.n)


def through_ptr() -> None:
    h = mkc(43)
    p: Ptr[HC] = h
    # a path through a Ptr
    print("through_ptr", sink_a(p.a))  # tpyc: warning(/copies A into owned storage/)


def getter() -> None:
    h = mkc(44)
    # a property getter
    print("getter", sink_a(h.pa))  # tpyc: warning(/copies A into owned storage/)


def narrowed() -> None:
    h = mkc(45)
    if h.o is not None:
        # a narrowed Optional payload
        print("narrowed", sink_a(h.o))  # tpyc: warning(/copies A into owned storage/)


def borrowed_param(h: HC) -> None:
    # a borrowed parameter as root
    print("borrowed_param", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)


G = mkc(47)


def global_root() -> None:
    # a module global as root
    print("global_root", sink_a(G.a))  # tpyc: warning(/copies A into owned storage/)


def inherited_del() -> None:
    h = HDChild(mka(52))
    # the owner inherits `__del__`
    print("inherited_del", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)


def mid_path_del() -> None:
    h = HHD(HD(mka(53)))
    # an owner in the middle of the path defines `__del__`
    print("mid_path_del", sink_a(h.hd.a))  # tpyc: warning(/copies A into owned storage/)


def nested_del() -> None:
    # an owner above the read's own owner defines `__del__`
    n = keep_l(HN(LA()).a.xs)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("nested_del", n)


def narrowed_root(c: bool) -> None:
    h: HC | None = None
    if c:
        h = mkc(54)
    if h is not None:
        # a narrowed root
        print("narrowed_root", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)


def same_call_path_borrower() -> None:
    h = mkc(55)
    r = h.l
    # `r` borrows `h` and is read in the call that takes `h.l`
    eat_l2(h.l, r)  # tpyc: warning(/copies list\[int32\] into owned storage/)


def unpack_store(o: Slot2) -> None:
    t = mkt()
    a, n = t
    # an unpack target still borrows the element
    o.a = t[0]  # tpyc: warning(/copies A into field/)
    print("unpack_store", a.b.w, n)


def unpack_append() -> None:
    t = mkt()
    a, n = t
    xs: list[A] = []
    # an unpack target still borrows the element
    xs.append(t[0])  # tpyc: warning(/copies A into owned storage/)
    print("unpack_append", a.b.w, n, len(xs))


async def task_path(k: KL) -> None:
    h = mkc(56)
    t = asyncio.create_task(read_l(h.l))
    # a task started on a coroutine borrows the path
    k.keep(h.l)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("task_path", await t)


async def task_name(k: KL) -> None:
    xs = [1, 2, 3]
    t = asyncio.create_task(read_l(xs))
    # a task started on a coroutine borrows the name
    k.keep(xs)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("task_name", await t)


def named_generator() -> None:
    h = mkc(57)
    for i in h.gen("named_generator"):
        # the running generator reads the root in its `finally`
        print("named_generator", keep_l(h.l))  # tpyc: warning(/copies list\[int32\] into owned storage/)
        return


def named_generator_whole() -> None:
    h = mkc(59)
    for i in h.gen("named_generator_whole"):
        # the running generator reads the root in its `finally`
        print("named_generator_whole", keep_hc(h))  # tpyc: warning(/copies HC into owned storage/)
        return


def named_with() -> None:
    h = HW(mka(58))
    with h:
        # `__exit__` reads the root after the call
        print("named_with", sink_a(h.a))  # tpyc: warning(/copies A into owned storage/)


def same_call_borrower() -> None:
    h = mkc(50)
    r = h.l
    # `r` borrows `h` and is read in the call that takes `h`
    two_h(h, r)  # tpyc: warning(/copies HC into owned storage/)


async def coroutine_loan() -> None:
    xs = [1, 2, 3]
    holder: list[list[int32]] = []
    c = work(xs)
    # the pending coroutine borrows `xs`
    holder.append(xs)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("coroutine_loan", await c, len(holder[0]))


async def run_async() -> None:
    await co()
    await pending_coroutine()
    await coroutine_loan()
    k = KL()
    await task_path(k)
    await task_name(k)


def main() -> None:
    free_fn()
    method()
    ctor()
    s = Slot()
    field_store(s)
    s.put()
    append()
    print("return_own", return_own().n)
    print("tuple_member", tuple_member()[0].n)
    nested()
    element_field()
    tuple_element_store(s)
    str_field()
    list_field()
    own_param_root(mkh(15))
    for v in gen():
        print("generator", v)
    try:
        print("error_return", er(False))
    except Fail:
        print("error_return failed")
    match_arm(1)
    consuming()
    consuming_nested()
    value_store(SB())
    print("value_member", value_member()[0])
    lx, ln = list_member()
    print("list_member", lx[0], ln)
    for le in list_element():
        print("list_element", le[0])
    temp_del()
    temp_del_reads()
    read_later()
    aliased()
    live_sub_borrow()
    same_stmt_root()
    same_stmt_paths()
    loop()
    closure()
    finally_reads()
    except_reads()
    comprehension()
    owner_del()
    nomove_leaf(HM(0))
    through_ptr()
    getter()
    narrowed()
    borrowed_param(mkc(46))
    global_root()
    inherited_del()
    mid_path_del()
    nested_del()
    narrowed_root(True)
    same_call_path_borrower()
    unpack_store(Slot2())
    unpack_append()
    same_call_borrower()
    named_generator()
    named_generator_whole()
    named_with()
    asyncio.run(run_async())


main()
