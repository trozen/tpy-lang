# `adopts=`: iter() and the combinators take a fresh argument over (no hold, no
# element copy -- P's __copy__ prints), while a named or by-reference one stays lent.
from typing import Iterator, Self
from tpy import char, int32, Own, Ptr, readonly


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __copy__(self) -> Own[Self]:
        print("COPY", self.v)
        return P(self.v)


def mk() -> Own[list[P]]:
    xs: list[P] = []
    xs.append(P(1))
    xs.append(P(2))
    return xs


def mkp(v: int32) -> Own[P]:
    return P(v)


def double(p: P) -> int32:
    return p.v * 2


def positive(p: P) -> bool:
    return p.v > 1


class Box:
    p: Ptr[readonly[P]] = None


def save(box: Box, p: P) -> int32:
    box.p = p
    return p.v


class Cur:
    i: int32
    n: int32

    def __init__(self, n: int32) -> None:
        self.i = 0
        self.n = n

    def __iter__(self) -> Self:
        print("Cur.__iter__")
        return self

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


class Noisy:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Own[Cur]:
        print("Noisy.__iter__")
        return Cur(self.n)


class Holder:
    xs: list[P]

    def __init__(self) -> None:
        self.xs = mk()

    def get(self) -> list[P]:
        return self.xs


def mk_set() -> Own[set[int32]]:
    return {7}


def mkd() -> Own[dict[str, int32]]:
    return {"a": 1, "b": 2}


def word(n: int32) -> str:
    return "xy" * n


def mk_ba() -> Own[bytearray]:
    return bytearray(b"\x05\x06")


def gen_adopt(n: int32) -> Iterator[int32]:
    # generator body: the adopted list lives in the combinator across the yield
    for i, x in enumerate(mk()):
        yield x.v + i + n


def main() -> None:
    # adopting combinators own a fresh list: no hold, no element copy
    pairs = list(enumerate(mk()))  # tpyc: ok
    for i, x in pairs:
        x.v += 100 * i
    print("adopt", [x.v for _, x in pairs])
    zs = list(zip(mk(), mk()))  # tpyc: ok
    print("zip", len(zs), zs[1][0].v, zs[1][1].v)
    print("map", list(map(double, mk())))
    print("filter", [x.v for x in filter(positive, mk())])
    rv: list[int32] = []
    for x in reversed(mk()):
        x.v += 5
        rv.append(x.v)
    print("reversed", rv)
    # a named list stays lent: a write through the pair reaches it
    src = mk()
    for _, y in enumerate(src):
        y.v += 30
    print("lent", src[0].v, src[1].v)
    # an adopted iterator still borrows what its argument borrows
    xs = [1, 2]
    e = list(enumerate(reversed(xs)))
    print("carried", e)
    live = enumerate(reversed(xs))
    xs.append(3)  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    print("carried_live", len(xs))
    # a record whose __iter__ builds a separate iterator is bound first
    # bound first: print streams its first argument before the second runs
    # (BUGS.md#print-arg-output-interleaves), and Noisy.__iter__ prints
    pe = list(enumerate(enumerate(Noisy(2))))
    print("pinned", pe)
    # an argument returned by reference is lent, not adopted: writes reach h.xs
    h = Holder()
    for _, y in enumerate(h.get()):  # tpyc: ok
        y.v += 40
    for z in iter(h.get()):  # tpyc: ok
        z.v += 3
        break
    print("by_ref", h.xs[0].v, h.xs[1].v)
    # iter() runs a fresh record's own __iter__, which builds a separate iterator
    ni = iter(Noisy(2))  # tpyc: ok
    print("noisy_made")
    n1 = next(ni, 0)
    print("noisy_iter", n1, next(ni, 0))
    # iter() owns a fresh container and returns a fresh self-iterator as is
    it = iter(mk())
    i1 = next(it, P(0)).v
    print("iter", i1, next(it, P(0)).v)
    ci = iter(Cur(2))
    print("self_made")
    c1 = next(ci, 0)
    print("self_iter", c1, next(ci, 0))
    si = iter(mk_set())
    s1 = next(si, 0)
    junk = [P(9), P(9), P(9)]
    print("set_iter", s1, next(si, 0), len(junk))
    di = iter(mkd())
    d1 = next(di, "")
    print("dict_iter", d1, next(di, ""))
    # a heap-sized string survives an allocation between pulls
    wi = iter(word(17))
    w1 = next(wi, char(" "))
    pad = word(30)
    print("str_iter", w1, list(wi), len(pad))
    bi = iter(mk_ba())
    b1 = next(bi, 0)
    print("bytearray_iter", b1, next(bi, 0))
    # a fresh generator expression is a self-iterator too
    gx = [1, 2]
    print("genexp_iter", list(iter(v + 1 for v in gx)))  # tpyc: ok
    # generator and comprehension positions
    print("generator", list(gen_adopt(3)))
    print("comp", [x.v + i for i, x in enumerate(mk())])
    # an address stored into a Ptr[readonly[P]] field outlives the call
    box = Box()
    n = save(box, mkp(11))
    filler = [P(90), P(91)]
    if box.p is not None:
        print("escape", n, box.p.v, filler[0].v)


main()
