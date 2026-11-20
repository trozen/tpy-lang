# A view of a by-value parameter stays valid wherever the parameter is read
# later: the body last use copies, as the hand-written function's does (a
# protocol method returning its own `U` at a view, an open-result local, a
# `with` whose `__exit__` reads a view, an async `try` / `finally` return, an
# open-result local read under a view or appended; its direct return moves).
# A protocol view return of a local builds, as on the hand-written function.
import asyncio
from typing import Callable, Protocol
from tpy import StrView, ValueType
from fixtures import Named, V


class W(ValueType):
    s: str

    def __init__(self, s: str) -> None:
        self.s = s

    def get(self) -> StrView:
        return "static"

    def get_u(self) -> StrView:
        return self.s


class Boxed[U](Protocol):
    def get_u(self) -> U: ...


def name_p(x: Named) -> StrView:
    return x.get()


def name_g[T: Named](x: T) -> StrView:
    return x.get()


def from_local_p() -> StrView:
    w = W("p")
    # the protocol form: `get` returns static storage, not a view of `w`
    return name_p(w)  # tpyc: ok


def from_local_g() -> StrView:
    w = W("g")
    return name_g(w)  # tpyc: ok


def eat(w: W) -> bool:
    return w.s == ""


def open_u[U, T: Boxed[U]](x: T, g: Callable[[T], bool]) -> None:
    v = x.get_u()
    # copies `x`: `v` views it below
    g(x)  # tpyc: ok
    print("lends open U", v)


def apply_local[T](x: T, f: Callable[[T], T]) -> T:
    acc = f(x)
    return f(acc)


def same(w: W) -> W:
    return w


def local_view[T: Named](x: T, f: Callable[[T], T], g: Callable[[T], bool]) -> None:
    acc = f(x)
    # spelled: an unannotated `w` would hold its own `str` copy
    w: StrView = acc.get()
    # the open-result local's last use copies, as the twin's `g(acc)`
    g(acc)  # tpyc: ok
    print("lends local view", w)


def local_append[T](x: T, f: Callable[[T], T], out: list[T]) -> None:
    acc = f(x)
    # copied into the list, as the twin's `out.append(acc)`
    out.append(acc)  # tpyc: warning(/may copy T into owned storage/)


def red[T](func: Callable[[T, T], T], xs: list[T]) -> T:
    acc = xs[0]
    for i in range(1, len(xs)):
        acc = func(acc, xs[i])
    # the direct return moves the dead local, as the twin's `return acc;`
    return acc


def twice[T](x: T, f: Callable[[T], T]) -> T:
    acc = f(x)
    acc = f(acc)
    # a rebound open-result local's direct return moves, as the twin's
    return acc


def fin_local[T: Named](x: T, f: Callable[[T], T]) -> T:
    acc = f(x)
    try:
        # staged: the local is read again in `finally`, so the result copies
        return acc
    finally:
        print("lends local finally reads", acc.get())


def with_local[T: Named](x: T, f: Callable[[T], T]) -> T:
    acc = f(x)
    cm = Guard(acc.get())
    with cm:
        # staged before `__exit__` reads its view of the local
        return acc


def fin_rebound[T: Named](x: T, f: Callable[[T], T]) -> T:
    acc = f(x)
    acc = f(acc)
    try:
        # staged: a rebound local read again in `finally` copies
        return acc
    finally:
        print("lends rebound finally reads", acc.get())


def with_rebound[T: Named](x: T, f: Callable[[T], T]) -> T:
    acc = f(x)
    acc = f(acc)
    cm = Guard(acc.get())
    with cm:
        # staged before `__exit__` reads its view of the rebound local
        return acc


def concat(a: str, b: str) -> str:
    return a + b


def same_v(v: V) -> V:
    return v


def eat_v(v: V) -> bool:
    return v.s == ""


class Guard:
    def __init__(self, w: StrView) -> None:
        self.w = w

    def __enter__(self) -> None:
        pass

    def __exit__(self, a: None, b: None, c: None) -> None:
        print("lends exit", self.w)


def with_return[T: Named](x: T) -> T:
    # `__exit__` prints a view of `x` after the result is staged (a short,
    # in-place string: a move would leave the view reading emptied storage)
    cm = Guard(x.get())
    with cm:
        # staged before `__exit__`: the result copies, as the twin's
        return x


def fin_sync[T: Named](x: T) -> T:
    try:
        # the deferred result copies, so `finally` still reads `x`
        return x
    finally:
        print("lends finally reads", x.get())


async def fin_return[T: Named](x: T) -> T:
    try:
        # the frame field is copied out, as the twin's
        return x
    finally:
        print("lends async finally reads", x.get())


async def co_main() -> None:
    ra = await fin_return(V("af"))
    print("lends async finally", ra.s)


def main() -> None:
    print("lends local", from_local_p(), from_local_g())
    open_u(W("u" * 30), eat)
    print("lends open local", apply_local(W("acc"), same).s)
    local_view(V("lv" * 20), same_v, eat_v)
    src = V("la")
    out: list[V] = []
    local_append(src, same_v, out)
    print("lends local append", out[0].s, src.s)
    print("lends local return", red(concat, ["a", "b", "c"]))
    tv = V("tw")
    print("lends local rebound return", twice(tv, same_v).s, tv.s)
    rl = fin_local(V("fl"), same_v)
    print("lends local finally", rl.s)
    rw = with_local(V("wl"), same_v)
    print("lends local with", rw.s)
    rbf = fin_rebound(V("rf"), same_v)
    print("lends rebound finally", rbf.s)
    rbw = with_rebound(V("rw"), same_v)
    print("lends rebound with", rbw.s)
    r = with_return(V("hello"))
    print("lends with", r.s)
    rf = fin_sync(V("fs"))
    print("lends finally", rf.s)
    asyncio.run(co_main())
