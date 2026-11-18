# Further shapes: a constructor storing the closure in a field, T = bytes,
# Send / Sync parameters, a sibling closure, and names that collide with the
# synthesized capture holder.
from typing import Callable, Protocol
from tpy import Send, Sync, int32

from fixtures import C, make_str


class Holder[T]:
    cb: Callable[[], None]

    def __init__(self, x: T) -> None:
        def g() -> None:  # tpyc: ok
            print("ctor", x)  # escapes through the field
        self.cb = g


def mk[T](x: T) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("bytes", x)
    return g


def show[U](tag: str, v: U) -> None:
    print(tag, v)


def mks[T](x: Send[T]) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        show("send", x)  # Send[T] holds the storage form as a bare T does
    return g


def mky[T](x: Sync[T]) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        show("sync", x)
    return g


def mksc(x: Send[C]) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("send.C", x)  # concrete Send[C]: still the caller's object
    return g


def scope_send() -> Callable[[], None]:
    loc = make_str(10)
    return mks(loc)


def sib[T](x: T) -> Callable[[], None]:
    def h() -> None:  # tpyc: ok
        print("sib", x)

    def g() -> None:  # tpyc: ok
        h()  # copying h copies its storage capture of x, unobservably
    h()
    return g


def mkn[T](x: T, __cap_x: T) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("names", x, __cap_x)  # the holder's name avoids the parameter
    return g


def mkg[T](x: T) -> Callable[[int32], None]:
    def g(__cap_x: int32) -> None:  # tpyc: ok
        print("names.g", x, __cap_x)  # ... and the nested def's own parameter
    return g


def mkl[T](x: T) -> Callable[[int32], None]:
    def g(k: int32) -> None:  # tpyc: ok
        __cap_x = k * 2  # ... and a local of the nested def
        print("names.l", x, __cap_x)
    return g


class Bump(Protocol):
    def bump(self) -> int32: ...


class Cnt:
    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> int32:
        self.n += 1
        return self.n


def mkm[T: Bump](x: T) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("mut", x.bump())  # writes the caller's object through the capture
    return g


def main() -> None:
    c = C(1)
    h = Holder(c)
    c.n = 2
    h.cb()
    hs = Holder(make_str(5))
    junk = make_str(6)
    hs.cb()
    fb = mk(b"xy" * 10)
    fb()
    fs = scope_send()
    fs()
    hc = mks(c)
    c.n = 4
    hc()
    hy = mky(c)
    c.n = 5
    hy()
    sc = mksc(c)
    c.n = 6
    sc()
    fsib = sib(c)
    c.n = 7
    fsib()
    mkn((1, 2), (3, 4))()
    mkg(42)(7)
    mkl(42)(7)
    cn = Cnt(1)
    fm = mkm(cn)
    cn.n = 10
    fm()
    fm()
    print("mut.after", cn.n)
    print("junk", len(junk))
