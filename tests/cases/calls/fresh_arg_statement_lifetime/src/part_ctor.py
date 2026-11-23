# Constructor positions: a fresh argument in a member initializer stays in the
# initializer (no default construction first), in a base initializer, and in
# the constructor body.
from typing import Protocol
from tpy import int32, Own, StrView
from fixtures import mk, total, grow, P, bump


class Pin:
    def __init__(self, k: int32 = 7) -> None:
        print("ctor Pin", k)
        self.k = k


class H:
    p: Pin

    def __init__(self) -> None:
        # member-init: the field is built once, from the fresh argument
        self.p = Pin(total(mk()))  # tpyc: ok


class W:
    p: Pin

    def __init__(self) -> None:
        # member-init, written parameter: still one construction
        self.p = Pin(grow([1, 2]))  # tpyc: ok


class NoDefault:
    def __init__(self, k: int32) -> None:
        self.k = k


class After:
    a: NoDefault
    b: NoDefault

    def __init__(self) -> None:
        self.a = NoDefault(total(mk()))
        # a no-default member after it: still initialized in the list
        self.b = NoDefault(bump(P(self.a.k)))  # tpyc: ok


class Named(Protocol):
    def tag(self) -> StrView: ...


class Box:
    name: str

    def __init__(self) -> None:
        self.name = "box-" + str(len("abcdef"))

    def tag(self) -> StrView:
        return self.name


def mkbox() -> Own[Box]:
    return Box()


def label(s: Named) -> StrView:
    return s.tag()


class Base:
    v: StrView
    n: int32

    def __init__(self, v: StrView) -> None:
        self.v = v
        self.n = len(v)


class D(Base):
    def __init__(self, b: Box) -> None:
        # base-init: the view `self.v` keeps is of the caller's box, bound to
        # a local there, so it outlives the object (a fresh box would die
        # with the initializer)
        super().__init__(label(b))  # tpyc: ok
        print("ctor base", self.n)


class Body:
    n: int32

    def __init__(self) -> None:
        print("ctor body")
        # constructor body: an ordinary statement
        self.n = total([4, 4]) + grow(mk())  # tpyc: ok


def main() -> None:
    h = H()
    print("ctor member", h.p.k)
    w = W()
    print("ctor written", w.p.k)
    a = After()
    print("ctor after", a.a.k, a.b.k)
    box = mkbox()
    d = D(box)
    print("ctor base view", d.v)
    b = Body()
    print("ctor body", b.n)
