# A `str`/`bytes` FIELD read at the OWNING sinks that take a copy of it: the
# insert argument (`append` / `insert`), the container-literal element, the
# comprehension element, and the subscript-assign KEY and VALUE. All five route
# through the one shared element / view->owned chokepoint, so an ordinary
# `parts = [r.name for r in rows]` lands the member read bare and the owned
# slot constructs from it (a VIEW-form member -- the `StrView` field below --
# takes the `std::string(v)` copy there).
# `str`/`bytes` are value types and the sink copies, so every section MUTATES
# the source field afterwards and prints both: the container keeps the
# pre-write value, which is what CPython's rebind-the-attribute does too.
from typing import Iterator

from tpy import Own, StrView, int32


class Inner:
    def __init__(self, tag: Own[bytes], name: Own[str], sv: StrView,
                 n: int32) -> None:
        self.tag = tag
        self.name = name
        self.sv = sv
        self.n = n


class Outer:
    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


class Gate:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    def __enter__(self) -> "Gate":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.hits += 1


# free function: the insert argument at every receiver the field ladder admits
def sec_insert() -> None:
    i = Inner(b"b1", "s1", "v1", 1)
    o = Outer(Inner(b"b2", "s2", "v2", 2))
    rows = [Inner(b"b3", "s3", "v3", 3)]
    accb: list[bytes] = []
    accs: list[str] = []
    accb.append(i.tag)  # tpyc: ok
    accb.append(o.inner.tag)  # tpyc: ok
    accb.insert(0, rows[0].tag)  # tpyc: ok
    accs.append(i.name)  # tpyc: ok
    accs.insert(0, o.inner.sv)  # tpyc: ok
    i.tag = b"ZZ"
    i.name = "ZZ"
    print("insert", accb[0], accb[1], accb[2], accs[0], accs[1],
          i.tag, i.name)


# free function: the container-literal element, field as dict KEY and as VALUE
def sec_literals() -> None:
    i = Inner(b"b1", "s1", "v1", 1)
    o = Outer(Inner(b"b2", "s2", "v2", 2))
    lb: list[bytes] = [i.tag, o.inner.tag]  # tpyc: ok
    ls: list[str] = [i.name, o.inner.sv]  # tpyc: ok
    st: set[str] = {i.name, o.inner.name}  # tpyc: ok
    dk: dict[str, int32] = {i.name: 1}  # tpyc: ok
    dv: dict[str, bytes] = {"k": i.tag}  # tpyc: ok
    i.tag = b"ZZ"
    i.name = "ZZ"
    print("lit", lb[0], lb[1], ls[0], ls[1], len(st), dk["s1"], dv["k"],
          i.tag, i.name)


# free function: the subscript-assign VALUE and KEY slots
def sec_setitem() -> None:
    i = Inner(b"b1", "s1", "v1", 1)
    o = Outer(Inner(b"b2", "s2", "v2", 2))
    dv: dict[str, bytes] = {}
    dk: dict[str, int32] = {}
    dv["k"] = i.tag  # tpyc: ok
    dv["j"] = o.inner.tag  # tpyc: ok
    dk[i.name] = 1  # tpyc: ok
    dk[o.inner.sv] = 2  # tpyc: ok
    i.tag = b"ZZ"
    i.name = "ZZ"
    print("setitem", dv["k"], dv["j"], dk["s1"], dk["v2"], i.tag, i.name)


# method: the same sinks off a chain rooted at `self`
class Runner:
    def __init__(self, o: Own[Outer]) -> None:
        self.o = o

    def run(self) -> None:
        acc: list[bytes] = []
        acc.append(self.o.inner.tag)  # tpyc: ok
        lit: list[str] = [self.o.inner.name]  # tpyc: ok
        d: dict[str, bytes] = {}
        d[self.o.inner.name] = self.o.inner.tag  # tpyc: ok
        print("method", acc[0], lit[0], d["s2"])


# constructor body: the insert argument and the literal element
class Snap:
    n: int32

    def __init__(self, o: Outer) -> None:
        acc: list[bytes] = []
        acc.append(o.inner.tag)  # tpyc: ok
        lit: list[str] = [o.inner.name]  # tpyc: ok
        self.n = len(acc)
        print("ctor", acc[0], lit[0])


# generator: the resumable frame's own locals take the same sinks
def sec_gen(o: Outer) -> Iterator[int32]:
    acc: list[bytes] = []
    acc.append(o.inner.tag)  # tpyc: ok
    lit: list[str] = [o.inner.name]  # tpyc: ok
    d: dict[str, int32] = {}
    d[o.inner.name] = 7  # tpyc: ok
    yield 1
    print("gen", acc[0], lit[0], d["s2"])


# comprehension element: the ordinary `[r.name for r in rows]`
def sec_comp(rows: list[Inner]) -> None:
    parts = [r.name for r in rows]  # tpyc: ok
    tags = [r.tag for r in rows]  # tpyc: ok
    uniq = {r.name for r in rows}  # tpyc: ok
    bymap = {r.name: r.n for r in rows}  # tpyc: ok
    joined = "-".join(r.name for r in rows)  # tpyc: ok
    # the write goes through a bound element: an element RECEIVER is not a
    # view-family field-write target (records/error_str_field_write_elem_receiver)
    r0 = rows[0]
    r0.name = "ZZ"
    print("comp", parts[0], parts[1], tags[0], len(uniq), bymap["s1"],
          joined, rows[0].name)


# with body / try-finally / match arm / closure
def sec_blocks(o: Outer) -> None:
    with Gate() as g:
        acc: list[bytes] = [o.inner.tag]  # tpyc: ok
        g.hits += 1
    print("with", acc[0], g.hits)
    try:
        ls: list[str] = [o.inner.name]  # tpyc: ok
    finally:
        print("try", ls[0])
    d: dict[str, int32] = {}
    match o.inner.n:
        case 2:
            d[o.inner.name] = 1  # tpyc: ok
        case _:
            d["other"] = 0
    print("match", d["s2"])

    def inner_build() -> str:
        acc2: list[str] = [o.inner.name]  # tpyc: ok
        return acc2[0]

    print("closure", inner_build())


def main() -> None:
    sec_insert()
    sec_literals()
    sec_setitem()
    Runner(Outer(Inner(b"b2", "s2", "v2", 2))).run()
    Snap(Outer(Inner(b"b2", "s2", "v2", 2)))
    for v in sec_gen(Outer(Inner(b"b2", "s2", "v2", 2))):
        print("gen yield", v)
    sec_comp([Inner(b"b1", "s1", "v1", 1), Inner(b"b2", "s2", "v2", 2)])
    sec_blocks(Outer(Inner(b"b2", "s2", "v2", 2)))


main()
