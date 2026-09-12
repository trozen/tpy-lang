# A walrus target inside a RESUMABLE generator is a frame field, so no C++ local
# may be declared for it -- a same-named block-scoped local would shadow the
# field and the write would die at the next suspension. Every generator here
# forces the resumable path (two yields) and reads the walrus target ACROSS a
# suspension, which is the only way the shadow is observable.
from typing import Iterator
from tpy import int32, Own


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# -- value scalar: the field is a bare `int32_t`.
def val_scalar() -> Iterator[int32]:
    yield -1
    i = 0
    while i < 2:
        yield (n := i * 10)       # tpyc: ok
        print("scalar resume", n)
        i += 1


# -- str: a value type owning a buffer, so a shadow reads back empty.
def val_str(words: list[str]) -> Iterator[int]:
    yield -1
    i = 0
    while i < len(words):
        yield len(s := words[i])  # tpyc: ok
        print("str resume", s)
        i += 1


def make_pair(i: int32) -> tuple[int32, int32]:
    return (i, i * 2)


# -- value tuple: both elements must survive the suspension.
def val_tuple() -> Iterator[int32]:
    yield -1
    i = 1
    while i < 3:
        yield (t := make_pair(i))[0]   # tpyc: ok
        print("tuple resume", t[0], t[1])
        i += 1


# -- owning non-value: the field is a `frame_slot<T>`, whose write must be an
# emplace (frame_slot has no operator=).
def owning() -> Iterator[int32]:
    yield -1
    i = 0
    while i < 2:
        yield len(xs := [i, i + 1])    # tpyc: ok
        print("owning resume", len(xs), xs[0])
        i += 1


# -- statement-borrow alias: the field must be a `T*` aliasing the caller's
# element, so a mutation after the boundary is visible to the caller (an owning
# copy would silently diverge from CPython here).
def borrow_alias(rows: list[list[int32]]) -> Iterator[int32]:
    yield -1
    i = 0
    while i < len(rows):
        yield len(row := rows[i])      # tpyc: ok
        row.append(99)
        print("borrow resume", len(row), row[0])
        i += 1


def pick(nodes: list[Node], i: int32) -> Node | None:
    if i < len(nodes):
        return nodes[i]
    return None


def value_of(n: Node | None) -> int32:
    if n is not None:
        return n.v
    return -9


# -- pointer-repr Optional: the field is a bare `T*` whose nullptr doubles as
# None, so a shadowed write makes the `is not None` branch silently vanish.
def opt_ptr(nodes: list[Node]) -> Iterator[int32]:
    yield -1
    i = 0
    while i < 3:
        yield value_of(m := pick(nodes, i))   # tpyc: ok
        if m is not None:
            m.v += 100
            print("optptr resume", m.v)
        else:
            print("optptr resume none")
        i += 1


def borrow_pair(n: Node) -> tuple[int32, Node]:
    return (n.v, n)


# -- reference-element borrow tuple: the pointer element must still address the
# caller's node after the resume, so the mutation is visible outside.
def borrow_tuple(nodes: list[Node]) -> Iterator[int32]:
    yield -1
    i = 0
    while i < len(nodes):
        yield (bt := borrow_pair(nodes[i]))[0]   # tpyc: ok
        bt[1].v += 1000
        print("borrow tuple resume", bt[0], bt[1].v)
        i += 1


def own_pair(i: int32) -> Own[tuple[int32, Node]]:
    return (i, Node(i * 5))


# -- owning tuple: an owning-call source cannot be aliased into a borrow-form
# field, so the frame needs the owning slot (a borrow field would point at the
# dead result temporary).
def own_tuple() -> Iterator[int32]:
    yield -1
    i = 1
    while i < 3:
        yield (ot := own_pair(i))[0]   # tpyc: ok
        print("own tuple resume", ot[0], ot[1].v)
        i += 1


class Boom(Exception):
    msg: str

    def __init__(self, msg: str) -> None:
        self.msg = msg


def raiser(i: int32) -> int32:
    if i > 0:
        raise Boom("bad")
    return i


# -- walrus off an exception-handler binding: the caught object is handler-
# scoped, so the frame must keep an owning COPY -- a `T*` alias would point
# past the catch block at the next resume.
def exc_binding() -> Iterator[int32]:
    yield -1
    i = 0
    while i < 2:
        try:
            yield raiser(i)
        except Boom as err:
            print("caught", (caught := err).msg)   # tpyc: ok
            yield 0
            print("exc resume", caught.msg)
        i += 1


# -- generator METHOD: the frame carries `__self` too, same field machinery.
class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def gen(self) -> Iterator[int32]:
        yield -1
        i = 0
        while i < self.n:
            yield (k := i * 10)   # tpyc: ok
            print("method resume", k)
            i += 1


def main() -> None:
    for a in val_scalar():
        print("got", a)
    for b in val_str(["alpha", "be"]):
        print("got", b)
    for c in val_tuple():
        print("got", c)
    for d in owning():
        print("got", d)

    rows = [[1, 2], [3, 4, 5]]
    for e in borrow_alias(rows):
        print("got", e)
    print("rows after", rows[0], rows[1])

    nodes = [Node(7), Node(8)]
    for f in opt_ptr(nodes):
        print("got", f)
    print("nodes after", nodes[0].v, nodes[1].v)

    more = [Node(1), Node(2)]
    for g in borrow_tuple(more):
        print("got", g)
    print("more after", more[0].v, more[1].v)

    for h in own_tuple():
        print("got", h)

    for x in exc_binding():
        print("got", x)

    s = Src(2)
    for m in s.gen():
        print("got", m)


main()
