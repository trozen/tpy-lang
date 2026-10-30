# Leaves an ownership record copies warn where it copies; `A.__copy__` prints
# each copy, and each section mutates the original after the boundary.
import asyncio
from typing import Any, Callable, Iterator, Self
from tpy import int32, Own, Fn, copy

# While `recording[0]` is set, copies are collected in `recorded` instead of
# printed: a tuple built with several copies constructs its elements in the
# C++ standard library's member order, which differs between toolchains.
recording = [False]
recorded: list[int32] = []


class A:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]

    def __copy__(self) -> Own[Self]:
        if recording[0]:
            recorded.append(self.items[0])
        else:
            print("COPY", self.items[0])
        return A(self.items[0])


class H:
    p: A
    q: A
    t: tuple[A, A]

    def __init__(self) -> None:
        self.p = A(0)
        self.q = A(0)
        self.t = (A(0), A(0))

    def put(self, p: tuple[Own[A], A]) -> None:
        # a mixed tuple parameter: element 0 moves, element 1 copies
        self.t = p  # tpyc: warning(/copies A into field \(tuple element 1\)/)


class Cell[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def peek(self) -> T:
        return self.v


def make(k: int32) -> Own[A]:
    if k > 100:
        raise ValueError("x")
    return A(k)


def joined_swapped(h: H, a: A, c: bool) -> None:
    if c:
        ft = (A(1), a)
    else:
        ft = (a, A(2))
    # each element borrows `a` on some path: both copy
    h.t = ft  # tpyc: warning(/tuple element 0/) warning(/tuple element 1/)


def borrowed_arm(h: H, k: int32) -> None:
    try:
        a = make(k)
    except ValueError:
        a = h.q
    # one bind borrows `h.q`: the hoisted local copies
    h.p = a  # tpyc: warning(/copies A into field/)


def if_borrowed_arm(h: H, out: list[A], c: bool) -> None:
    if c:
        v = h.q
    else:
        v = make(7)
    # one if arm borrows `h.q`: the append copies, never moving out of it
    out.append(v)  # tpyc: warning(/copies A into owned storage/)


def borrowed_arm_finally(h: H, k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = h.q
    try:
        # a return under a finally whose binding borrows: the capture copies
        # before the finally, never moving out of `h.q`
        return a  # tpyc: warning(/copies A into owned storage/)
    finally:
        a.items.append(9)


def finally_rebind(k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    try:
        # the finally reads and rebinds `a`: the return copies before it
        # (BUGS.md#finally-mutate-then-rebind-return)
        return a  # tpyc: warning(/copies A into owned storage/)
    finally:
        print("finally_rebind reads", len(a.items))
        a = A(9)


async def own_param_suspend(a: Own[A]) -> Own[A]:
    try:
        # a return under a finally that awaits is captured before it: the
        # owned parameter copies
        return a  # tpyc: warning(/copies A into owned storage/)
    finally:
        await asyncio.sleep(0)
        a.items.append(2)


async def hoisted_suspend(k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    try:
        # ... and so does a try-hoisted owning local
        return a  # tpyc: warning(/copies A into owned storage/)
    finally:
        await asyncio.sleep(0)
        a.items.append(2)


def nested_return(p: A) -> Own[tuple[tuple[A, int32], A]]:
    t = ((A(19), 2), p)
    # the nested owned element moves out at the last use; element 1, which
    # `t` borrows, copies
    return t  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


def captured(h: H) -> tuple[Own[A], int32]:
    t = (A(3), 2)

    def peek() -> int32:
        return len(t[0].items)
    print("captured peek", peek())
    # a closure still reads `t`: the return copies it
    return t  # tpyc: warning(/copies tuple\[A, int32\] into owned storage/)


def ident[T](x: T) -> T:
    return x


def keep[T](xs: list[T], x: T) -> None:
    y = ident(x)
    # a generic local bound off a lending call borrows: the append copies
    xs.append(y)  # tpyc: warning(/may copy T into owned storage if not a value type/)


def keep_peek[T](out: list[T], c: Cell[T]) -> None:
    y = c.peek()
    # ... and off a lending method of a generic class
    out.append(y)  # tpyc: warning(/may copy T into owned storage if not a value type/)


def chained(h: H, a: A) -> None:
    # a chained store copies into each field
    h.p = h.q = a  # tpyc: warning(/copies A into field/)


def lam_value(a: A) -> Own[A]:
    f: Callable[[A], A] = lambda q: q  # tpyc: warning(/copies A into owned storage/)
    # a lambda's by-value result copies its borrowed parameter; the call
    # through the `Callable` value hands that copy on
    # (BUGS.md#callable-value-borrow-return-copies-unwarned)
    return f(a)


def through(f: Fn[[A], Own[A]], a: A) -> Own[A]:
    return f(a)


def lam_or(b: A) -> Own[A]:
    # an `Own` lambda result takes the existing operand of an `or` by copy
    return through(lambda q: q or A(0), b)  # tpyc: warning(/copies A into owned storage/)


def lam_select(b: A, flag: bool) -> Own[A]:
    # ... and the existing arm of a select
    return through(lambda q: q if flag else A(0), b)  # tpyc: warning(/copies A into owned storage/)


def lam_pointer_local(xs: list[A], c: bool) -> Callable[[], A]:
    q = xs[0] if c else None
    assert q is not None
    # a by-value lambda result over a pointer-bound local copies its object
    return lambda: q  # tpyc: warning(/copies A into owned storage/) warning(/may keep its own copy of 'q'/)


def keep_rebound[T](xs: list[T], x: T, z: T) -> None:
    # a rebound generic local copies at each bind, unwarned, and each append
    # moves that copy (BUGS.md#generic-lending-local-copies-unwarned)
    y = ident(x)
    xs.append(y)  # tpyc: ok
    y = ident(z)
    xs.append(y)  # tpyc: ok


def keep_next[T](it: Iterator[T], xs: list[T]) -> None:
    try:
        r = next(it)
        # a generic stub's result bound to a hoisted local: the slot holds
        # a copy made at the bind, which the append moves on; the hedge
        # speaks for that copy at the read the record keeps
        # (BUGS.md#generic-lending-local-copies-unwarned: the bind's own
        # copy is not registered)
        xs.append(r)  # tpyc: warning(/may copy T into owned storage if not a value type/)
    except StopIteration:
        pass


def mixed_literal(b: A) -> None:
    a = A(15)
    # a mixed literal converts member by member: the dead `a` moves, the
    # live `b` copies
    xs: list[tuple[A, A]] = [(a, b)]  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)
    b.items.append(9)
    x0, x1 = xs[0]
    print("mixed_literal", len(x0.items), len(x1.items), len(b.items))


def lift_one(a: A) -> Own[list[tuple[A, int32]]]:
    t = (a, 0)
    # one warned copy of the borrowed leaf: the literal builds its one
    # member in place, no second copy out of an initializer list
    return [t]  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def lift_one_copy(a: A) -> Own[list[tuple[A, int32]]]:
    t = (a, 0)
    # the explicit copy is the only one
    return [copy(t)]  # tpyc: ok


def lift_dict(a: A) -> Own[dict[int32, tuple[A, int32]]]:
    t = (a, 0)
    # the dict twin: one warned copy, the map built in place
    return {1: t}  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def key() -> int32:
    print("key runs")
    return 1


def lift_dict_running(a: A) -> Own[dict[int32, tuple[A, int32]]]:
    t = (a, 0)
    # a key that runs code keeps the brace-init and its left-to-right order,
    # and its extra copy (BUGS.md#list-literal-owned-elem-initializer-copy)
    return {key(): t}  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def count_list(xs: list[A]) -> int32:
    return len(xs)


def lift_arg(a: A) -> int32:
    # a call argument keeps the brace-init: two copies
    # (BUGS.md#list-literal-owned-elem-initializer-copy)
    return count_list([a])  # tpyc: warning(/copies A into owned storage/)


def lift_any(xs: list[int32]) -> Any:
    # a store into `Any` keeps the brace-init too
    v: Any = [xs]  # tpyc: warning(/copies list\[int32\] into owned storage/)
    return v


class Wrap:
    a: A

    def __init__(self, a: Own[A]) -> None:
        self.a = a


class Holder:
    w: Wrap

    def __init__(self) -> None:
        self.w = Wrap(A(30))

    def wrap(self) -> Wrap:
        return self.w


def get_w(ho: Holder) -> Wrap:
    return ho.w


def mk_w(n: int32) -> Own[Wrap]:
    return Wrap(A(n))


class K:
    a: A

    def __init__(self, ho: Holder) -> None:
        # constructor: a member of a lending call's result copies
        self.a = ho.wrap().a  # tpyc: warning(/copies A into field/)

    def pull(self, ho: Holder) -> None:
        # method: the same, off a lending free function
        self.a = get_w(ho).a  # tpyc: warning(/copies A into field/)


def lend_member(h: H, ho: Holder) -> None:
    # free function: a member of a lending call's result copies ...
    h.p = get_w(ho).a  # tpyc: warning(/copies A into field/)
    # ... and a member of a fresh call's result moves out of the temporary
    h.q = mk_w(31).a  # tpyc: ok


def lend_next(h: H, ws: list[Wrap], ho: Holder) -> None:
    # a member of `next(it, default)`'s result names the element or the
    # default the call lends: it copies
    it = iter(ws)
    h.p = next(it, ho.w).a  # tpyc: warning(/copies A into field/)


def callable_member(h: H, ho: Holder) -> None:
    # a member of a `Callable` value's result copies: the signature does not
    # prove the result fresh, and the target here lends `ho.w`
    fn: Callable[[Holder], Wrap] = get_w
    h.p = fn(ho).a  # tpyc: warning(/copies A into field/)


def mk() -> Iterator[Own[A]]:
    yield A(12)


def main() -> None:
    h = H()
    a = A(5)
    recording[0] = True
    joined_swapped(h, a, True)
    recording[0] = False
    print("joined_swapped copies", sorted(recorded))
    a.items.append(9)
    t0, t1 = h.t
    print("joined_swapped", len(t0.items), len(t1.items))
    borrowed_arm(h, 200)
    h.q.items.append(9)
    print("borrowed_arm", len(h.p.items), len(h.q.items))
    out: list[A] = []
    if_borrowed_arm(h, out, True)
    out[0].items.append(8)
    print("if_borrowed_arm", len(out[0].items), len(h.q.items))
    # `t0, t1` borrow `h.t`, which growing `h.q.items` leaves in place.
    r = borrowed_arm_finally(h, 200)  # tpyc: ok
    print("borrowed_arm_finally", len(r.items), len(h.q.items))
    fr = finally_rebind(1)
    print("finally_rebind", fr.items[0])
    c = captured(h)
    print("captured", c[1])
    a1 = A(1)
    xs: list[A] = []
    keep(xs, a1)
    a1.items.append(9)
    print("keep", len(xs[0].items), len(a1.items))
    cell = Cell(A(2))
    outs: list[A] = []
    keep_peek(outs, cell)
    cell.v.items.append(9)
    print("keep_peek", len(outs[0].items), len(cell.v.items))
    b = A(4)
    chained(h, b)
    b.items.append(9)
    print("chained", len(h.p.items), len(h.q.items), len(b.items))
    d = A(5)
    h.put((A(6), d))
    d.items.append(9)
    u0, u1 = h.t
    print("put", len(u0.items), len(u1.items))
    e = A(7)
    r1 = lam_value(e)
    e.items.append(9)
    print("lam_value", len(r1.items), len(e.items))
    f = A(8)
    r2 = lam_or(f)
    r3 = lam_select(f, True)
    f.items.append(9)
    print("lam_or lam_select", len(r2.items), len(r3.items), len(f.items))
    ys = [A(10)]
    g = lam_pointer_local(ys, True)
    r4 = g()
    ys[0].items.append(9)
    print("lam_pointer_local", len(r4.items), len(ys[0].items))
    zs: list[A] = []
    keep_rebound(zs, A(16), A(17))
    print("keep_rebound", len(zs), zs[0].items[0], zs[1].items[0])
    nx: list[A] = []
    srcs = [A(14)]
    keep_next(iter(srcs), nx)
    srcs[0].items.append(9)
    print("keep_next", len(nx[0].items), len(srcs[0].items))
    mixed_literal(A(18))
    s1 = asyncio.run(own_param_suspend(A(20)))
    print("own_param_suspend", len(s1.items))
    s2 = asyncio.run(hoisted_suspend(21))
    print("hoisted_suspend", len(s2.items))
    np = A(22)
    nr = nested_return(np)
    np.items.append(9)
    inner = nr[0]
    last = nr[1]
    first = inner[0]
    print("nested_return", len(first.items), len(last.items), len(np.items))
    la = A(23)
    lifted = lift_one(la)
    la.items.append(9)
    le = lifted[0]
    lf = le[0]
    print("lift_one", len(lf.items), len(la.items))
    copied = lift_one_copy(la)
    la.items.append(9)
    ce = copied[0]
    cf = ce[0]
    print("lift_one_copy", len(cf.items), len(la.items))
    ld = lift_dict(la)
    la.items.append(9)
    de = ld[1]
    df = de[0]
    print("lift_dict", len(df.items), len(la.items))
    lr = lift_dict_running(la)
    la.items.append(9)
    re_ = lr[1]
    rf = re_[0]
    print("lift_dict_running", len(rf.items), len(la.items))
    na = lift_arg(la)
    print("lift_arg", na)
    lx = [1]
    av = lift_any(lx)
    lx.append(2)
    print("lift_any", av is not None, len(lx))
    ho = Holder()
    lend_member(h, ho)
    ho.w.a.items.append(9)
    print("lend_member", len(h.p.items), len(h.q.items), len(ho.w.a.items))
    k = K(ho)
    ho.w.a.items.append(9)
    print("lend_member ctor", len(k.a.items), len(ho.w.a.items))
    k.pull(ho)
    ho.w.a.items.append(9)
    print("lend_member method", len(k.a.items), len(ho.w.a.items))
    nw: list[Wrap] = []
    lend_next(h, nw, ho)
    ho.w.a.items.append(9)
    print("lend_next", len(h.p.items), len(ho.w.a.items))
    callable_member(h, ho)
    ho.w.a.items.append(9)
    print("callable_member", len(h.p.items), len(ho.w.a.items))


main()
# module level: the loop variable rebound to existing storage copies, warned
# with the binding inside the loop, as the function twin is
mb = A(13)
mout: list[A] = []
for ma in mk():
    ma = mb
    mout.append(ma)  # tpyc: warning(/copies A into owned storage; .*'ma' is bound to existing storage/)
mb.items.append(9)
print("module_rebind", len(mout[0].items), len(mb.items))
