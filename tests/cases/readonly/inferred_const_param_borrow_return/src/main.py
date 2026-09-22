# A method that never mutates `self` is inferred const, but only the RECEIVER
# is proven: a return that borrows a PARAMETER keeps its declared mutable type
# (`Rec& pick(B& other) const`), as the free-function spelling does. Every
# section writes through the result and reads the source afterwards, so a
# const-projected or copied return would show.
from typing import Optional
from tpy import int32, readonly


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    m: Rec
    o: Optional[Rec]
    rs: list[Rec]
    ns: list[int32]

    def __init__(self, x: int32) -> None:
        self.m = Rec(x)
        self.o = Rec(x + 100)
        self.rs = [Rec(x + 200)]
        self.ns = [x]

    def bump(self) -> None:
        self.m.x += 1


class K:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def rec(self, other: B) -> Rec:
        return other.m

    def opt(self, other: B) -> Optional[Rec]:
        return other.o

    def recs(self, other: B) -> list[Rec]:
        return other.rs

    def ints(self, other: B) -> list[int32]:
        return other.ns

    def whole(self, other: B) -> B:
        return other

    # the first parameter is mutated in the body, the second is only lent out
    def second(self, first: B, other: B) -> Rec:
        first.bump()
        return other.m

    # the method's OWN type parameter: the inferred-const signature picks
    # `val_or_ref_t<U>` / `param_val_or_ref_t<U>` over the const forms, which
    # only a REFERENCE instantiation mutated through the result can tell apart
    def echo[U](self, v: U) -> U:
        return v

    # DECLARED readonly: every parameter is readonly, so the result is const
    @readonly
    def peek(self, other: B) -> Rec:
        return other.m


class G[T]:
    tag: T

    def __init__(self, tag: T) -> None:
        # the field owns its value, and a reference-typed T would be copied in
        self.tag = tag  # tpyc: warning(/may copy T into field/)

    # method of a generic record
    def rec(self, other: B) -> Rec:
        return other.m


def free_rec(other: B) -> Rec:
    return other.m


def main() -> None:
    k = K()
    b = B(1)

    r = k.rec(b)  # tpyc: ok
    r.x += 10
    print("rec", b.m.x)

    o = k.opt(b)  # tpyc: ok
    if o is not None:
        o.x += 10
    p = b.o
    if p is not None:
        print("opt", p.x)

    rs = k.recs(b)  # tpyc: ok
    rs[0].x += 10
    print("recs", b.rs[0].x)

    ns = k.ints(b)  # tpyc: ok
    ns.append(5)
    print("ints", len(b.ns))

    w = k.whole(b)  # tpyc: ok
    w.bump()
    print("whole", b.m.x)

    a = B(50)
    s = k.second(a, b)  # tpyc: ok
    s.x += 100
    print("second", a.m.x, b.m.x)

    g = G(7)
    gr = g.rec(b)  # tpyc: ok
    gr.x += 1000
    print("generic", b.m.x)

    fr = free_rec(b)
    fr.x += 1
    print("free", b.m.x)

    own = Rec(7)
    out = k.echo(own)  # tpyc: ok
    out.x += 1
    print("generic_method", own.x)

    # the declared-readonly twin is read, never written through
    print("peek", k.peek(b).x)


main()
