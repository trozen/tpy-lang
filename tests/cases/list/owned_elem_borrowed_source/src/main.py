# An inferred container element is OWNED storage even when its source hands
# back a borrow (a borrow-returning method, a class parameter): the element
# holds a warned copy, never a C++ reference. Copies by design, so each
# section only reads through the element (CPython would alias it).
from typing import Iterator

from tpy import int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    r: R

    def __init__(self, n: int32) -> None:
        self.r = R(n)

    def rec_m(self) -> R:
        return self.r


# comprehension of a borrow-returning method call (vector and fixed-size forms)
def comp_method() -> None:
    b = B(1)
    xs = [b.rec_m() for _ in range(2)]  # tpyc: warning(/copies R into owned storage/)
    ks = ["a", "b", "c"]
    ys = [b.rec_m() for _ in ks]  # tpyc: warning(/copies R into owned storage/)
    print("comp_method", xs[1].n, len(ys))


# single-element list literal of a class parameter
def literal_param(p: R) -> None:
    ps = [p]  # tpyc: warning(/copies R into owned storage/)
    print("literal_param", ps[0].n)


# generator: a for-source literal over class parameters
def gen_params(a: R, b: R) -> Iterator[int32]:
    for x in [a, b]:  # tpyc: warning(/copies R into owned storage/)
        yield x.n


def pick(src: dict[str, R]) -> R:
    return src["a"]


# an empty dict seeded by a bodied borrow-returning call: the value type is
# the owned R (never a reference), the store a warned copy. (Appending the
# same call to a list is BUGS.md#borrow-call-append-rejects.)
def empty_seeded(src: dict[str, R]) -> None:
    e = {}
    e["k"] = pick(src)  # tpyc: warning(/copies R into container/)
    e["j"] = R(4)
    print("empty_seeded", e["k"].n, e["j"].n)


def main() -> None:
    comp_method()
    empty_seeded({"a": R(2)})
    literal_param(R(3))
    for v in gen_params(R(4), R(5)):
        print("gen_params", v)


main()
