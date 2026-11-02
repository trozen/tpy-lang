# A protocol member counts as written when ANY same-name overload of the
# argument type writes (C++ picks the overload inside the protocol template):
# the caller stays non-const whatever the overloads return or take (twice,
# widen, broad), even when only a const overload is reachable (unreached, the
# rule's cost). Writers are declared first where CPython's dispatch stand-in
# must run them too.
from typing import Protocol, Sized
from tpy import int8, int32, readonly, dispatch


class Feed(Protocol):
    def feed(self, x: int32) -> int32: ...


class Feed0(Protocol):
    def feed(self) -> int32: ...


class Twice:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def feed(self, x: int32) -> int32:
        self.hits += 1
        return 20

    @dispatch
    @readonly
    def feed(self, x: int32) -> int32:
        return 10


class Widen:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def feed(self) -> int8:
        self.hits += 1
        return 20

    @dispatch
    @readonly
    def feed(self) -> int32:
        return 10


class FeedList(Protocol):
    def feed(self, x: list[int32]) -> int32: ...


class Broad:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def feed(self, x: Sized) -> int32:
        self.hits += 1
        return 20

    @dispatch
    @readonly
    def feed(self, x: list[int32]) -> int32:
        return 10


class Unreached:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly
    def feed(self, x: list[int32]) -> int32:
        return 10

    @dispatch
    @readonly(False)
    def feed(self, x: int32) -> int32:
        self.hits += 1
        return 20


def use(p: Feed) -> int32:
    return p.feed(1)


def use0(p: Feed0) -> int32:
    return p.feed()


# the caller stays `Twice&`, so the writing overload runs
def k_twice(t: Twice) -> int32:
    return use(t)


# the writing overload returns another type: still the one a mutable
# receiver selects, so the caller stays `Widen&`
def k_widen(w: Widen) -> int32:
    return use0(w)


def use_list(p: FeedList, xs: list[int32]) -> int32:
    return p.feed(xs)


# a writing overload taking a wider parameter (`Sized`) than the protocol's
# member: the caller stays `Broad&`, and the template's call runs it
def k_broad(b: Broad, xs: list[int32]) -> int32:
    return use_list(b, xs)


# the deliberate cost of counting every overload: the writing `feed(int32)`
# is one the protocol's call cannot reach, yet the caller stays `Unreached&`
# -- one const lost, no behavior change
def k_unreached(u: Unreached, xs: list[int32]) -> int32:
    return use_list(u, xs)


def main() -> None:
    t = Twice()
    print("twice", k_twice(t), t.hits)
    w = Widen()
    print("widen", k_widen(w), w.hits)
    xs = [1]
    b = Broad()
    print("broad", k_broad(b, xs), b.hits)
    u = Unreached()
    print("unreached", k_unreached(u, xs), u.hits)


main()
