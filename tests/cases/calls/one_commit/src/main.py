# Every call kind commits through one commit (a constructor binds as a function);
# no sole `@readonly __init__` section: BUGS.md#list-scalar-union-param-call-rejects.
import asyncio
from typing import Callable, Iterator, TypedDict, Unpack
from tpy import copy, dispatch, error_return, int32, Own, ReturnException


class Bad(Exception, ReturnException):
    pass


class P:
    a: int32
    b: int32

    def __init__(self, a: int32, *, b: int32 = 0) -> None:
        self.a = a
        self.b = b


class G[T]:
    a: T
    b: int32

    def __init__(self, a: T, *, b: int32 = 0) -> None:
        self.a = copy(a)
        self.b = b


class Opt(TypedDict):
    a: int32


class K:
    n: int32

    def __init__(self, **kw: Unpack[Opt]) -> None:
        self.n = kw["a"]


class Cb[T]:
    r: str

    def __init__(self, cb: Callable[[T], T], x: T) -> None:
        self.r = f"{cb(x)}"


class CbFixed[T]:
    r: str

    def __init__(self, cb: Callable[[int32], int32], x: T) -> None:
        self.r = f"{cb(5)}:{x}"


class Sink:
    n: int32

    def __init__(self, a: int32, *, xs: list[int32]) -> None:
        xs.append(a)
        self.n = len(xs)


class Store:
    def __init__(self) -> None:
        pass

    def __setitem__(self, k: int32, v: Sink) -> None:
        v.n += k


class Ap:
    def __init__(self) -> None:
        pass

    def ap[T, U](self, f: Callable[[T], U], v: T) -> U:
        return f(v)


def put(s: Store, v: Sink) -> None:
    # The store's value is the setter's argument: `v` stays mutable. The
    # warning is the known false one: no copy is emitted
    # (BUGS.md#setitem-store-container-sink).
    s[2] = v  # tpyc: warning(/copies Sink into container/)


class DBag[T]:
    v: T
    items: list[T]

    def __init__(self, v: T = 0) -> None:
        self.v = copy(v)
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(copy(x))

    def touch(self, xs: list[int32]) -> None:
        xs.append(9)

    def wipe(self, xs: list[Sink]) -> None:
        xs.clear()

    @error_return(Bad)
    def check(self, n: int32) -> int32:
        if n < 0:
            raise Bad
        return n


def touch_pending(xs: list[int32]) -> None:
    b = DBag()
    # A method on a receiver whose type argument is still open: it writes
    # `xs`, which stays a mutable parameter.
    b.touch(xs)
    b.put(5)


class Wipe[T]:
    items: list[T]

    def __init__(self, xs: list[Sink]) -> None:
        xs.clear()
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))


class WipeKw[T]:
    items: list[T]

    def __init__(self, k: int32 = 0, *, xs: list[Sink]) -> None:
        # Drops the last element: a borrow of the first stays valid.
        del xs[len(xs) - 1]
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))


@dispatch
def takes_wipe(w: Wipe[int32]) -> int32:
    return 1


@dispatch
def takes_wipe(w: str) -> int32:
    return 2


def deferred_borrow_kw_gap() -> None:
    xs = [Sink(1, xs=[]), Sink(2, xs=[])]
    e = xs[0]
    # The keyword argument binds past an omitted default, as at
    # `WipeKw[int32](xs=xs)`; `e` is read after it.
    w = WipeKw(xs=xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    w.put(5)
    print("deferred borrow kw gap:", e.n, len(xs))


def deferred_borrow_operand() -> None:
    xs = [Sink(1, xs=[])]
    e = xs[0]
    n = e.n
    # The construction is an operand of an overloaded call, first analyzed
    # at its ranking's probe.
    r = takes_wipe(Wipe(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    print("deferred borrow operand:", n, len(xs), r)


def deferred_borrow_loop() -> None:
    for i in range(2):
        xs = [Sink(i, xs=[])]
        e = xs[0]
        n = e.n
        # Warned once, however many times the loop body is analyzed.
        w = Wipe(xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
        w.put(5)
        print("deferred borrow loop:", n, len(xs))


def deferred_borrow() -> None:
    xs = [Sink(1, xs=[])]
    e = xs[0]
    # Read before the construction: after it `e` dangles.
    n = e.n
    # Completed only at `w.put(5)`, the construction still conflicts with
    # the element borrow `e` live where it is written.
    w = Wipe(xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    w.put(5)
    print("deferred borrow:", n, len(xs), w.items)


def pending_borrow() -> None:
    b = DBag()
    xs = [Sink(1, xs=[])]
    e = xs[0]
    # Read before the call: after it `e` dangles.
    n = e.n
    # A method on a receiver whose type argument is still open conflicts
    # with the element borrow `e`, as its explicit twin does.
    b.wipe(xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    b.put(5)
    print("pending borrow:", n, len(xs))


def pending_error_return() -> None:
    b = DBag()
    # An `@error_return` method on a pending receiver, handled: returning,
    # then raising.
    ok = 0
    try:
        ok = b.check(3)
        r = b.check(-1)
    except Bad:
        r = 7
    b.put(r)
    print("pending error_return:", ok, r, b.items)


@error_return(Bad)
def pending_error_return_propagates() -> int32:
    b = DBag()
    # Inside an `@error_return(Bad)` body the error propagates.
    r = b.check(-1)
    b.put(r)
    return r


def deferred_param(xs: list[Sink]) -> None:
    # A parameter passed to a construction completed later: the clear in
    # `__init__` reaches the caller's list.
    w = Wipe(xs)  # tpyc: ok
    w.put(5)


class WipeKwN:
    def __init__(self, k: int32 = 0, *, xs: list[Sink]) -> None:
        del xs[len(xs) - 1]


def drop_kw(k: int32 = 0, *, xs: list[Sink]) -> None:
    xs.append(Sink(k, xs=[]))


class Dropper:
    def __init__(self) -> None:
        pass

    def drop(self, k: int32 = 0, *, xs: list[Sink]) -> None:
        xs.append(Sink(k, xs=[]))


def kw_gap_call(xs: list[Sink], ys: list[Sink]) -> None:
    # The ordinary-call twin: the keyword binds past an omitted default and
    # the write reaches the caller's list, function and method alike.
    drop_kw(xs=xs)
    Dropper().drop(xs=ys)


def kw_gap_param(xs: list[Sink], ys: list[Sink]) -> None:
    # The keyword binds past an omitted default; the constructor's clear
    # reaches the caller's list, plain and explicit generic alike.
    WipeKwN(xs=xs)
    WipeKw[int32](xs=ys)


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Num(TypedDict):
    n: int32


class KwC[T]:
    def __init__(self, **kw: Unpack[Num]) -> None:
        kw["n"] += 1

    def put(self, v: T) -> None:
        pass


def kw_bump(**kw: Unpack[Num]) -> int32:
    kw["n"] += 1
    return kw["n"]


def touch(xs: list[Sink]) -> int32:
    # Drops the last element: a borrow of the first stays valid.
    del xs[len(xs) - 1]
    return 1


@dispatch
def takes_kwc(c: KwC[int32]) -> int32:
    return 1


@dispatch
def takes_kwc(c: str) -> int32:
    return 2


def routed_keyword(b: Box) -> None:
    # A routed keyword builds a fresh dict: the writes to `kw` reach no
    # argument, so `b` stays a const parameter, pending or explicit.
    c = KwC(n=b.n)
    c.put(1)
    KwC[int32](n=b.n)
    print("routed keyword:", kw_bump(n=b.n), b.n)


def region_borrow() -> None:
    xs = [Sink(1, xs=[]), Sink(2, xs=[]), Sink(3, xs=[])]
    e = xs[0]
    # The routed keyword's argument is analyzed at the construction,
    # where the borrow `e` is live (read below), as at its explicit twin.
    c = KwC(n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    c.put(1)
    KwC[int32](n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    print("region borrow:", e.n, len(xs))


def region_gen() -> Iterator[int32]:
    xs = [Sink(1, xs=[]), Sink(2, xs=[]), Sink(3, xs=[])]
    e = xs[0]
    # Generator body: completed at `c.put(1)`, warned at the construction.
    c = KwC(n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    c.put(1)
    KwC[int32](n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    yield e.n + len(xs)


async def region_async() -> int32:
    xs = [Sink(1, xs=[]), Sink(2, xs=[]), Sink(3, xs=[])]
    e = xs[0]
    # Async body: the same.
    c = KwC(n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    c.put(1)
    KwC[int32](n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    return e.n + len(xs)


def region_closure() -> int32:
    def inner() -> int32:
        xs = [Sink(1, xs=[]), Sink(2, xs=[]), Sink(3, xs=[])]
        e = xs[0]
        # Nested def: the same.
        c = KwC(n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
        c.put(1)
        KwC[int32](n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
        return e.n + len(xs)
    return inner()


def region_loop() -> None:
    for i in range(2):
        xs = [Sink(1, xs=[]), Sink(2, xs=[])]
        e = xs[0]
        # Warned once, however many times the loop body is analyzed.
        c = KwC(n=touch(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
        c.put(1)
        print("region loop:", e.n, len(xs))


def region_operand() -> None:
    xs = [Sink(1, xs=[]), Sink(2, xs=[])]
    e = xs[0]
    # The construction is an operand of an overloaded call.
    r = takes_kwc(KwC(n=touch(xs)))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    print("region operand:", e.n, len(xs), r)


def region_late_borrow() -> None:
    xs = [Sink(1, xs=[]), Sink(2, xs=[]), Sink(3, xs=[])]
    # No borrow is live at the construction: the later one at the
    # completing use conflicts with nothing it passes.
    c = KwC(n=touch(xs))  # tpyc: ok
    KwC[int32](n=touch(xs))  # tpyc: ok
    e = xs[0]
    c.put(1)
    print("region late borrow:", e.n, len(xs))


def touch_num(xs: list[Sink]) -> Own[Num]:
    del xs[len(xs) - 1]
    return Num(n=1)


def dstar_late_borrow() -> None:
    xs = [Sink(1, xs=[]), Sink(2, xs=[]), Sink(3, xs=[])]
    # A `**expr` is analyzed at the construction too.
    c = KwC(**touch_num(xs))  # tpyc: ok
    KwC[int32](**touch_num(xs))  # tpyc: ok
    e = xs[0]
    c.put(1)
    print("dstar late borrow:", e.n, len(xs))


def twice(v: int32) -> int32:
    return v * 2


class Maker:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def go(self) -> None:
        # Method body: the keyword-only argument binds by name.
        print("method body:", P(self.n, b=self.n * 2).b)


def gen(n: int32) -> Iterator[int32]:
    # Generator body: the keyword-only argument binds by name.
    yield P(n, b=n + 1).b


def main() -> None:
    # Free-function body: a keyword-only constructor argument by name.
    p = P(1, b=2)
    print("ctor kwonly:", p.a, p.b)
    # The keyword-only default omitted.
    print("ctor kwonly default:", P(1).b)
    # A list passed by keyword-only argument is the caller's own list.
    xs = [7]
    sink = Sink(1, xs=xs)
    print("ctor kwonly list:", sink.n, xs)
    put(Store(), sink)
    print("setitem operand:", sink.n)
    # A construction completed at a later use: the default fits the type
    # argument the use gives.
    d = DBag()
    d.put(1)
    print("deferred default:", d.v, d.items)
    ys = [1]
    touch_pending(ys)
    print("pending receiver method:", ys)
    deferred_borrow()
    deferred_borrow_kw_gap()
    deferred_borrow_operand()
    deferred_borrow_loop()
    pending_borrow()
    pending_error_return()
    try:
        pending_error_return_propagates()
    except Bad:
        print("pending error_return propagated")
    ps = [Sink(1, xs=[])]
    deferred_param(ps)
    print("deferred param:", len(ps))
    qs = [Sink(1, xs=[]), Sink(2, xs=[])]
    rs = [Sink(1, xs=[]), Sink(2, xs=[])]
    kw_gap_param(qs, rs)
    print("kw gap param:", len(qs), len(rs))
    kw_gap_call(qs, rs)
    print("kw gap call:", len(qs), len(rs))
    routed_keyword(Box(3))
    region_borrow()
    region_operand()
    for v in region_gen():
        print("region gen:", v)
    print("region async:", asyncio.run(region_async()))
    print("region closure:", region_closure())
    region_loop()
    region_late_borrow()
    dstar_late_borrow()
    # A generic method types a lambda argument as a generic call does.
    print("generic method lambda:", Ap().ap(lambda v: v + 1, 5))
    g = G("x", b=3)
    print("generic ctor kwonly:", g.a, g.b)
    # A `**kwargs: Unpack[TD]` constructor builds its TypedDict.
    print("ctor kwargs:", K(a=3).n)
    # A generic constructor types a lambda argument as a generic call does.
    print("generic ctor lambda:", Cb(lambda v: v + 1, 5).r)
    print("generic ctor fn:", Cb(twice, 5).r)
    print("generic ctor fixed lambda:", CbFixed(lambda v: v + 1, 1).r)
    Maker(3).go()
    for b in gen(4):
        print("generator body:", b)


main()
