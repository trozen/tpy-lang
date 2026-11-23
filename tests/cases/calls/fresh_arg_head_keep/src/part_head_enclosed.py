# A name a `with` binds and reads after the statement keeps only the head
# object it borrows -- the manager, or the root of a borrowed manager's chain.
from typing import Iterator, Self
from tpy import Own, Ptr, StrView, int32, nocopy


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def __enter__(self) -> T:
        return self.v

    def __exit__(self, a, b, c) -> None:
        pass


def label(s: Src) -> str:
    return "label-long-enough-to-heap-" + str(s.n) * 10


class Box:
    src: Src

    def __init__(self, n: int32) -> None:
        self.src = Src(n)


class Mgr:
    b: Box

    def __init__(self, b: Own[Box]) -> None:
        self.b = b

    def __enter__(self) -> Box:
        return self.b

    def __exit__(self, a, b, c) -> None:
        # the manager mutates what the target refers to on exit
        self.b.src.n += 100


@nocopy
class NcMgr:
    b: Box

    def __init__(self, b: Own[Box]) -> None:
        self.b = b

    def __enter__(self) -> Box:
        return self.b

    def __exit__(self, a, b, c) -> None:
        pass


class CM:
    name: str

    def __init__(self, n: int32) -> None:
        self.name = "cm-long-enough-to-heap-" + str(n) * 10

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        self.name += "!"


class Mid:
    cm: CM

    def __init__(self, n: int32) -> None:
        self.cm = CM(n)

    def get(self) -> CM:
        return self.cm


class Top:
    mid: Mid

    def __init__(self, n: int32) -> None:
        self.mid = Mid(n)

    def inner(self) -> Mid:
        return self.mid


class PtrCM:
    s: Ptr[Src]

    def __init__(self, s: Src) -> None:
        self.s = s

    def __enter__(self) -> Src:
        return self.s

    def __exit__(self, a, b, c) -> None:
        pass


def mk(n: int32) -> int32:
    return n


def values() -> None:
    # a generic manager's value target is declared at its type for this call
    with Holder(label(Src(1))) as t:  # tpyc: ok
        pass
    junk = [str(i) * 40 for i in range(20)]
    print("enclosed value", t[0:12], len(t), len(junk))


def references(flag: bool) -> None:
    # the manager the target refers to lives on, bound twice under one name
    with Mgr(Box(mk(1))) as t:  # tpyc: ok
        t.src.n += 1
    t.src.n += 10
    print("enclosed ref", t.src.n)
    if flag:
        with Mgr(Box(mk(2))) as t:  # tpyc: ok
            pass
    print("enclosed ref again", t.src.n)
    for i in range(2):
        # a reference target bound inside the head's scope, read after it
        with Mgr(Box(mk(i + 5))) as u:  # tpyc: ok
            pass
        u.src.n += 1
        print("enclosed ref loop", u.src.n)
    # a @nocopy manager is held where it was built, never copied
    with NcMgr(Box(mk(7))) as w:  # tpyc: ok
        pass
    w.src.n += 1
    print("enclosed nocopy", w.src.n)


def chains() -> None:
    # a borrowed manager keeps the fresh receiver at the root of its chain
    with Mid(1).get() as a:  # tpyc: ok
        pass
    junk = [str(k) * 40 for k in range(20)]
    print("enclosed chain", a.name[0:12], a.name[-1], len(junk))
    with Top(2).inner().get() as b:  # tpyc: ok
        pass
    other = b
    b.name += "?"
    print("enclosed deep", other.name[-2:])


class ViewCM:
    text: str

    def __init__(self, s: Src) -> None:
        self.text = "view-cm-text-long-enough-to-heap-" + str(s.n) * 20

    def __enter__(self) -> StrView:
        return self.text

    def __exit__(self, a, b, c) -> None:
        pass


class Pt:
    x: int32
    name: str
    box: Box

    def __init__(self, x: int32) -> None:
        self.x = x
        self.name = "pt-name-long-enough-to-heap-" + str(x) * 10
        self.box = Box(x)


def build(s: Src) -> Own[Pt]:
    return Pt(s.n)


def views() -> None:
    # a view target borrows its manager's storage past the statement
    with ViewCM(Src(2)) as v:  # tpyc: ok
        pass
    junk = ["x" * 47 for _ in range(20)]
    print("enclosed view", v[0:12], v[-3:], len(junk))


def matches(p: Pt) -> None:
    # a capture of an owned subject is a copy or a move out of it: nothing
    # to enclose (a reference-type capture read after it is refused,
    # `match.field_bind_ptr_hoist`)
    match build(Src(4)):  # tpyc: ok
        case Pt(x=x, name=nm):
            pass
    junk = ["z" * 50 for _ in range(20)]
    nm += "!"
    print("enclosed match owned", x, nm[-3:], len(junk))
    # a capture of a borrowed subject aliases storage that already outlives
    # the statement; a write after it shows through the subject
    match p:  # tpyc: ok
        case Pt() as pb:
            pass
    pb.box.src.n += 10
    print("enclosed match borrowed", p.box.src.n)


def rebound() -> None:
    # a reference target rebound after the statement
    with PtrCM(Src(5)) as n:  # tpyc: ok
        print("enclosed rebound in", n.n)
    other = Src(8)
    n = other
    other.n += 1
    print("enclosed rebound", n.n)


def gen(k: int32) -> Iterator[str]:
    # in a generator the borrowed manager's receiver lives on the frame (a
    # body that suspends: BUGS.md#with-borrow-call-manager-faces)
    with Mid(k).get() as a:  # tpyc: ok
        pass
    yield a.name[0:3]
    junk = [str(j) * 30 for j in range(20)]
    yield a.name[-1] + str(len(junk))


def owned_gen(k: int32) -> Iterator[str]:
    for i in range(k):
        # an owned target, read after a later yield: nothing to enclose
        with Holder(label(Src(i))) as t:  # tpyc: ok
            pass
        yield "a"
        junk = [str(j) * 30 for j in range(20)]
        yield t[-2:] + str(len(junk))


def rebound_gen() -> Iterator[int32]:
    # a reference target rebound after the statement, in a generator
    with PtrCM(Src(5)) as n:  # tpyc: ok
        yield n.n
    other = Src(8)
    n = other
    yield 0
    other.n += 1
    yield n.n


def main() -> None:
    values()
    views()
    matches(Pt(30))
    print("enclosed owned gen", list(owned_gen(2)))
    print("enclosed rebound gen", list(rebound_gen()))
    references(True)
    references(False)
    chains()
    rebound()
    print("enclosed gen", list(gen(3)))
