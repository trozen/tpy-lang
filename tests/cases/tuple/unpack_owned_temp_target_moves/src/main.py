# An unpack target off a call that hands its element over owns it and moves at
# a consuming read, as `a = st.pop()` does; a lending source keeps its alias.
from typing import Iterator
from tpy import int32, Own, nocopy


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    item: A

    def __init__(self) -> None:
        self.item = A(1)


def sink(tag: str, x: Own[list[str]]) -> Own[list[str]]:
    x.append("s")
    print(tag, len(x), x[0], x[-1])
    return x


def sinka(tag: str, x: Own[A]) -> Own[A]:
    x.v += 10
    print(tag, x.v)
    return x


def sinkn(tag: str, x: Own[N]) -> Own[N]:
    x.v += 10
    print(tag, x.v)
    return x


def mk() -> Own[tuple[A, int32]]:
    return (A(1), 2)


def mk_elem() -> tuple[Own[A], int32]:
    return (A(20), 3)


def mkn() -> Own[tuple[N, int32]]:
    return (N(3), 4)


def lend(h: Holder) -> tuple[A, int32]:
    return (h.item, 1)


# free function: a popped tuple's list element moves into the sink.
def free_pop() -> None:
    st = [(["x"], 1)]
    (a, n) = st.pop()
    a.append("pre")
    r = sink("free_pop", a)  # tpyc: ok
    r.append("post")
    print("free_pop", n, len(st), len(r))


# free function: an `Own[tuple]` call's element moves into the sink.
def free_mk() -> None:
    a, n = mk()
    a.v += n
    r = sinka("free_mk", a)  # tpyc: ok
    r.v += 100
    print("free_mk", r.v)


# return: the owned target is the function's owned result.
def take_a() -> Own[A]:
    a, n = mk()
    a.v += n
    return a  # tpyc: ok


def ret() -> None:
    r = take_a()
    r.v += 100
    print("ret", r.v)


# method: a `self.make()` source inside a method body.
class Maker:
    k: int32

    def __init__(self) -> None:
        self.k = 5

    def make(self) -> Own[tuple[A, int32]]:
        return (A(self.k), 1)

    def use(self) -> None:
        a, n = self.make()
        r = sinka("method", a)  # tpyc: ok
        r.v += 100
        print("method", r.v, n)


# loop: every pass moves its popped element.
def loop() -> None:
    ps = [(A(1), 1), (A(2), 2)]
    while ps:
        a, n = ps.pop()
        a.v += n
        r = sinka("loop", a)  # tpyc: ok
        r.v += 100
        print("loop", r.v, len(ps))


# nocopy: the element moves, no copy is asked for.
def nocopy_elem() -> None:
    a, n = mkn()
    r = sinkn("nocopy", a)  # tpyc: ok
    r.v += 100
    print("nocopy", r.v, n)


# rebound: the owned target points into the holder; the sink moves out of
# it and the rebind re-points it.
def rebound_sink_first() -> None:
    p, k = mk()
    r = sinka("rebound_sink_first", p)  # tpyc: ok
    p = A(9)
    p.v += k
    r.v += 100
    print("rebound_sink_first", p.v, r.v)


# rebound: the rebind comes first, the sink takes the new object.
def rebound_rebind_first() -> None:
    p, k = mk()
    p.v += 50
    print("rebound_rebind_first", p.v)
    p = A(9)
    r = sinka("rebound_rebind_first", p)  # tpyc: ok
    r.v += 100
    print("rebound_rebind_first", r.v, k)


# dict.pop: the popped value's element moves into the sink.
def dict_pop() -> None:
    d = {1: (A(4), 1)}
    a, n = d.pop(1)
    r = sinka("dict_pop", a)  # tpyc: ok
    r.v += 100
    print("dict_pop", r.v, n, len(d))


# closure: a nested def reads the target after a write through it.
def closure() -> None:
    a, n = mk()

    def f() -> int32:
        return a.v + n
    a.v = 7  # tpyc: ok
    print("closure", f())


# loop, rebound: each pass sinks its popped element, then rebinds the name.
def loop_rebound() -> None:
    ps = [(A(1), 1), (A(2), 2)]
    while ps:
        a, n = ps.pop()
        r = sinka("loop_rebound", a)  # tpyc: ok
        a = A(0)
        a.v += n
        r.v += 100
        print("loop_rebound", a.v, r.v)


# generator: a declared-`Own` element off a call keeps the frame's move into
# its field (an `Own[tuple]` call source moves its elements into frame
# storage the same, tests/cases/tuple/unpack_hands_over).
def gen() -> Iterator[int32]:
    a, n = mk_elem()
    yield n
    a.v += 1
    print("gen", a.v)
    r = sinka("gen", a)  # tpyc: ok
    r.v += 100
    print("gen", r.v)


def run_gen() -> None:
    for x in gen():
        print("gen", x)


# module level: the target is a global slot the element moves into.
ga, gn = mk()  # tpyc: ok


def module_level() -> None:
    ga.v += 30
    print("module_level", ga.v, gn)


# read-only: the target stays a reference into the holder.
def read_only() -> None:
    a, n = mk()
    print("read_only", a.v, n)  # tpyc: ok


# inverse: a tuple NAME source keeps its warned copy at the sink
# (BUGS.md#own-tuple-param-unpack-field-copy).
def inv_name() -> None:
    t = mk()
    a, n = t
    r = sinka("inv_name", a)  # tpyc: warning(/copies A into owned storage/)
    print("inv_name", n)


# inverse: a borrowing call's target aliases the callee's storage.
def inv_lend() -> None:
    h = Holder()
    a, n = lend(h)
    a.v = 42  # tpyc: ok
    print("inv_lend", h.item.v, n)


# inverse: an element borrowed before the unpack stays shared.
def inv_elem_borrow() -> None:
    t = mk()
    b = t[0]
    a, n = t
    a.v = 5
    print("inv_elem_borrow", b.v, n)


# inverse: a rebind of the tuple shares its elements.
def inv_rebind() -> None:
    t = mk()
    t2 = t
    a, n = t
    a.v = 6
    print("inv_rebind", t2[0].v, n)


# inverse: a field store at a non-last use copies, warned (the copy is the
# acknowledged divergence, so nothing writes after it).
def inv_field() -> None:
    h = Holder()
    a, n = mk()
    h.item = a  # tpyc: warning(/copies A into/)
    print("inv_field", a.v, h.item.v, n)


# inverse: a store at a non-last use copies, warned.
def inv_store() -> None:
    keep: list[A] = []
    a, n = mk()
    keep.append(a)  # tpyc: warning(/copies A into owned storage/)
    print("inv_store", a.v, len(keep), n)


def main() -> None:
    free_pop()
    free_mk()
    ret()
    Maker().use()
    loop()
    nocopy_elem()
    rebound_sink_first()
    rebound_rebind_first()
    dict_pop()
    closure()
    loop_rebound()
    run_gen()
    module_level()
    read_only()
    inv_name()
    inv_lend()
    inv_elem_borrow()
    inv_rebind()
    inv_field()
    inv_store()


main()
