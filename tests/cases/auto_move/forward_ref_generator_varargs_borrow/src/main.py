# A `*args` pack is one argument slot holding many operands, and a callee
# borrowing the pack borrows every one of them -- so a later consume of a
# packed element must demote to a copy, exactly as the non-varargs sibling
# (auto_move/forward_ref_generator_borrow) does.
from typing import Iterator
from tpy import int32, Own


class Caller:
    # Defined BEFORE Collector, so the method generator is forward-referenced
    # and only the registration-time borrow stamp can see the pack.
    def run(self) -> int32:
        a: list[int32] = [1, 2, 3]
        b: list[int32] = [4]
        c = Collector()
        g = c.sizes(a, b)
        # the method frame views the caller's arg array just as a free
        # generator's does, so consuming a packed element demotes to a copy
        n = drop(a)  # tpyc: warning(/copies/)
        total = 0
        for v in g:
            total += v
        return total + n


class Collector:
    def sizes(self, *xs: list[int32]) -> Iterator[int32]:
        n = 0
        for s in xs:
            n += len(s)
            yield n
        yield -1


def main():
    # forward-referenced generator: the frame views the caller's arg array,
    # so the pack elements stay borrowed while the generator is alive.
    # Annotated: a bare list literal infers Array[int32, N], not list.
    a: list[int32] = [1, 2, 3]
    b: list[int32] = [4]
    g = gen(a, b)
    print("gen:", drop(a))  # tpyc: warning(/copies/)
    for v in g:
        print("gen:", v)

    # plain function (no frame): the returned borrow of a pack element keeps
    # the whole pack borrowed just the same.
    c: list[int32] = [5, 6]
    d: list[int32] = [7]
    r = first(c, d)
    print("plain:", drop(c))  # tpyc: warning(/copies/)
    # mutate through the returned borrow and read the SOURCE: a copy at the
    # return would leave `c` at 2 while `r` grew.
    r.append(99)
    print("plain:", len(c), len(r))

    # method generator, forward-referenced from a class declared above it
    print("method:", Caller().run())

    # keyword-only borrow behind the pack: its borrow index is the one the
    # pack would shift, so the sink on it must still demote
    e: list[int32] = [1, 2]
    f: list[int32] = [3]
    h: list[int32] = [4, 5, 6, 7]
    gk = kwgen(e, f, extra=h)
    print("kwonly:", drop(h))  # tpyc: warning(/copies/)
    for v in gk:
        print("kwonly:", v)

    # ... and the pack elements in front of it keep their own indices
    p: list[int32] = [1, 2]
    q: list[int32] = [3]
    s: list[int32] = [9]
    gp = kwgen(p, q, extra=s)
    print("kwpack:", drop(p))  # tpyc: warning(/copies/)
    for v in gp:
        print("kwpack:", v)

    # transitive return: forward's result borrows a pack element of first(),
    # which reaches the caller's argument through the forwarded pack
    t: list[int32] = [8, 9, 10]
    u: list[int32] = [11]
    fr = forward(t, u)
    print("fwd:", drop(t))  # tpyc: warning(/copies/)
    # same aliasing check as the plain section, one hop further out
    fr.append(99)
    print("fwd:", len(t), len(fr))

    # two hops of forwarding: the pack is re-spelled at every hop, so the
    # borrow must still land on the caller's own argument.
    w: list[int32] = [1, 2]
    y: list[int32] = [3]
    f2 = forward2(w, y)
    print("fwd2:", drop(w))  # tpyc: warning(/copies/)
    f2.append(99)
    print("fwd2:", len(w), len(f2))


def gen(*xs: list[int32]) -> Iterator[int32]:
    n = 0
    for s in xs:
        n += len(s)
        yield n
    yield -1


def kwgen(*xs: list[int32], extra: list[int32]) -> Iterator[int32]:
    n = 0
    for s in xs:
        n += len(s)
        yield n
    yield len(extra)


def first(*xs: list[int32]) -> list[int32]:
    return xs[0]


def forward(*xs: list[int32]) -> list[int32]:
    return first(*xs)


def forward2(*zs: list[int32]) -> list[int32]:
    return forward(*zs)


def drop(xs: Own[list[int32]]) -> int32:
    store: list[list[int32]] = []
    store.append(xs)
    return len(store[0])


main()
