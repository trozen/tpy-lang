# `copy()` over a borrow-returning CALL source at the three sinks beside the
# return and the element slot -- a field write, a setitem, a container-literal
# element -- plus the two sources whose copy is NOT the copy-construct tail
# and must keep taking their own arms. COPY SEMANTICS ARE THE POINT: every
# copied source is mutated afterwards and read back, and CPython agrees
# because `copy()` deep-copies there.
import tpy
from tpy import int32, copy


class Payload:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Dog:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Cat:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    p: Payload
    items: list[int32]

    def __init__(self) -> None:
        self.p = Payload(1)
        self.items = [1, 2]

    def brec(self) -> Payload:
        return self.p

    def bctr(self) -> list[int32]:
        return self.items


class Sink:
    q: Payload
    box: list[int32]

    def __init__(self, h: Holder) -> None:
        # The sources bound to locals first: these writes read body locals,
        # so they are body assignments, not member-inits.
        p = h.brec()
        items = h.bctr()
        self.q = copy(p)  # tpyc: ok
        self.box = copy(items)  # tpyc: ok


def rewrite(s: Sink, h: Holder) -> None:
    # The plain field write, same two families.
    s.q = copy(h.brec())  # tpyc: ok
    s.box = copy(h.bctr())  # tpyc: ok


def setitem(h: Holder) -> int32:
    xs = [Payload(0)]
    xs[0] = copy(h.brec())  # tpyc: ok
    h.p.v = 5
    return xs[0].v


def container_element(h: Holder) -> int32:
    xs = [copy(h.brec()), copy(h.p)]  # tpyc: ok
    h.p.v = 6
    return xs[0].v + xs[1].v


def qualified_decl(h: Holder) -> int32:
    # The module-QUALIFIED spelling at a plain decl reaches the generic call
    # tail rather than the decl sink's own copy row -- same one-step source.
    dup = tpy.copy(h.brec())
    h.p.v = 4
    return dup.v


def variant_copy(pick: bool) -> int32:
    # NOT the copy-construct tail: a ptr-variant union's copy is the
    # active-member deep copy, its own arm.
    u: Dog | Cat = Dog(1)
    if not pick:
        u = Cat(2)
    dup = copy(u)
    if isinstance(u, Dog):
        u.n = 8
    if isinstance(dup, Dog):
        return dup.n
    return 0


class OptSink:
    opt: Payload | None

    def __init__(self) -> None:
        self.opt = None


def optional_copy(o: Payload | None) -> int32:
    # NOT the copy-construct tail either: a pointer-repr Optional is handed
    # back unwrapped, and the SINK's storage lift is what copies.
    s = OptSink()
    s.opt = copy(o)  # tpyc: ok
    if o is not None:
        o.v = 9
    if s.opt is not None:
        return s.opt.v
    return 0


def main() -> None:
    h = Holder()
    s = Sink(h)
    rewrite(s, h)
    h.p.v = 3
    h.items.append(4)
    print(s.q.v, len(s.box), h.p.v, len(h.items))
    print(setitem(Holder()), container_element(Holder()),
          qualified_decl(Holder()))
    print(variant_copy(True), optional_copy(Payload(2)))


main()
