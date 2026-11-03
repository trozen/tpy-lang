# An existing object (element, field, dict element, property read, ternary of
# lvalues, Ptr member, literal-seeded name) at a runtime binding's protocol
# parameter binds in place, as at its concrete twin, at every position.
# Locals are annotated where the unannotated literal would settle on a
# fixed-size array rather than the list the section reads.
from __future__ import annotations
import asyncio
from typing import Iterator
from tpy import int32, Own, Ptr, String, StrView


def pick(xs: list[int32]) -> list[int32]:
    return xs


class H:
    xs: list[int32]
    names: list[str]
    bs: list[bytes]
    flags: list[bool]

    def __init__(self) -> None:
        self.xs = [3, 1, 2]
        self.names = ["a", "b"]
        self.bs = [b"x", b"y"]
        self.flags = [True, False]

    @property
    def p(self) -> list[int32]:
        return self.xs

    # method: field and property reads
    def m(self) -> None:
        print("method", sum(self.xs), sorted(self.xs), ",".join(self.names),  # tpyc: ok
              len(self.p), list(enumerate(self.xs)))  # tpyc: ok


class C:
    total: int32

    # ctor: element reads of a parameter
    def __init__(self, ll: list[list[int32]]) -> None:
        self.total = sum(ll[0]) + max(ll[1]) + len(ll[0])  # tpyc: ok


def dbl(v: int32) -> int32:
    return v * 2


def pos(v: int32) -> bool:
    return v > 1


# free function: the folding bindings at every spelling
def folds() -> None:
    # annotated: a ternary of a literal-seeded local's elements rejects
    # (BUGS.md#seeded-element-ternary-binding-arg-rejects)
    ll: list[list[int32]] = [[1, 2], [3, 4]]
    d: dict[str, list[int32]] = {"k": [5, 6]}
    bl: list[list[bool]] = [[True, False]]
    h = H()
    p: Ptr[H] = h
    t = [True, True]
    c = len(ll[0]) > 1
    print("element", sum(ll[0]), max(ll[1]), min(ll[1]), sorted(ll[0]),  # tpyc: ok
          any(bl[0]), all(bl[0]), len(ll[0]))  # tpyc: ok
    print("dict", sum(d["k"]), max(d["k"]), sorted(d["k"]), len(d["k"]))  # tpyc: ok
    print("field", sum(h.xs), min(h.xs), sorted(h.xs), any(h.flags),  # tpyc: ok
          all(h.flags), len(h.xs))  # tpyc: ok
    print("property", sum(h.p), max(h.p), sorted(h.p), len(h.p))  # tpyc: ok
    print("ternary", sum(ll[0] if c else ll[1]),  # tpyc: ok
          sorted(ll[1] if c else ll[0]), len(ll[0] if c else ll[1]))  # tpyc: ok
    print("ptr", sum(p.xs), max(p.xs), sorted(p.xs), all(p.flags), len(p.xs))  # tpyc: ok
    print("literal", all(t), any(t), len(t))  # tpyc: ok


# free function: the lazy bindings at every spelling
def lazy() -> None:
    ll: list[list[int32]] = [[1, 2], [3, 4]]
    d: dict[str, list[int32]] = {"k": [5, 6]}
    h = H()
    p: Ptr[H] = h
    print("enumerate", list(enumerate(ll[0])), list(enumerate(h.xs)),  # tpyc: ok
          list(enumerate(d["k"])))  # tpyc: ok
    print("zip", list(zip(ll[0], ll[1])), list(zip(h.xs, p.xs)))  # tpyc: ok
    print("iter", list(iter(ll[0])), list(iter(d["k"])), list(iter(h.p)))  # tpyc: ok
    print("reversed", list(reversed(ll[1])), list(reversed(h.xs)))  # tpyc: ok
    print("map", list(map(dbl, ll[0])), list(map(dbl, d["k"])),  # tpyc: ok
          list(map(dbl, p.xs)))  # tpyc: ok
    print("filter", list(filter(pos, ll[1])), list(filter(pos, h.xs)))  # tpyc: ok
    for i, v in enumerate(ll[0]):  # tpyc: ok
        print("for-enumerate", i, v)


# free function: the join bindings
def joins() -> None:
    names: list[list[str]] = [["p", "q"]]
    bl: list[list[bytes]] = [[b"a", b"b"]]
    h = H()
    sep = String("|")
    # the annotation is the subject: StrView.join
    sv: StrView = ","
    seeded = ["u", "v"]
    print("str.join", "-".join(names[0]), ",".join(h.names), "+".join(seeded))  # tpyc: ok
    print("String.join", sep.join(names[0]))  # tpyc: ok
    print("StrView.join", sv.join(names[0]))  # tpyc: ok
    print("bytes.join", b"-".join(bl[0]), b"+".join(h.bs))  # tpyc: ok


# write-through: each lazy binding sees an in-place write to the element
# made after it is created and before it is driven
def write_through() -> None:
    ll: list[list[int32]] = [[1, 2], [3, 4]]
    e = enumerate(ll[0])
    z = zip(ll[0], ll[1])
    i = iter(ll[1])
    r = reversed(ll[0])
    m = map(dbl, ll[1])
    f = filter(pos, ll[0])
    ll[0][0] = 10
    ll[1][0] = 30
    print("write-through", list(e), list(z), list(i), list(r), list(m), list(f))


# generator body: bindings in a for head and in a yield
def gen(ll: list[list[int32]]) -> Iterator[int32]:
    for i, v in enumerate(ll[0]):  # tpyc: ok
        yield i * 10 + v
    for a, b in zip(ll[0], ll[1]):  # tpyc: ok
        yield a * b
    yield sum(ll[1]) + len(sorted(ll[0]))  # tpyc: ok


# async body: bindings across awaits
async def co(ll: list[list[int32]]) -> int32:
    n = 0
    for i, v in enumerate(ll[0]):  # tpyc: ok
        await asyncio.sleep(0)
        n += v + i
    for a, b in zip(ll[0], ll[1]):  # tpyc: ok
        await asyncio.sleep(0)
        n += a * b
    return n + sum(ll[1]) + max(ll[0]) + len(sorted(ll[1]))  # tpyc: ok


# comprehension: element and field reads in the element expression
def comprehension() -> None:
    ll: list[list[int32]] = [[1, 2], [3, 4]]
    h = H()
    print("comprehension", [sum(ll[k]) for k in range(2)],  # tpyc: ok
          [max(h.xs) for _ in range(2)])  # tpyc: ok


# closure: an element of a captured local
def closure() -> int32:
    # annotated: BUGS.md#nested-def-tuple-of-captured-pending-list-ice
    ll: list[list[int32]] = [[1, 2], [3]]

    def inner() -> int32:
        return sum(ll[1]) + len(ll[0])  # tpyc: ok
    return inner()


class G[T]:
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item

    def get(self) -> T:
        return self.item


class K(G[list[int32]]):
    def __init__(self, item: Own[list[int32]]) -> None:
        super().__init__(item)

    # qualified / super() call results at a binding's parameter
    def via(self) -> int32:
        return len(G.get(self)) + sum(super().get())  # tpyc: ok

    # a write through the borrowed result reaches self.item
    def recv(self) -> None:
        super().get().append(9)  # tpyc: ok
        G.get(self).append(8)

    # a local bound to the borrowed result aliases self.item
    def bind(self) -> None:
        x = G.get(self)  # tpyc: ok
        x.append(7)
        y = super().get()
        y.append(6)


def qualified() -> None:
    k = K([1, 2])
    print("qualified", k.via())
    k.recv()
    print("qualified-recv", k.item)
    k.bind()
    print("qualified-bind", k.item)


class Sector:
    flags: list[bool]

    def __init__(self) -> None:
        self.flags = [True, False, True]


# a container member of a Ptr[record] parameter
def count_flags(sector: Ptr[Sector]) -> int32:
    return len(sector.flags)  # tpyc: ok


def ptr_param() -> None:
    s = Sector()
    print("ptr-param", count_flags(s))


# a bytearray element, a bytearray tuple element and a name-or-fresh ternary
def other_objects(t: tuple[bytearray, int32]) -> None:
    bas: list[bytearray] = []
    bas.append(bytearray(b"ab"))
    xs = [1, 2]
    c = len(xs) > 1
    print("other", len(bas[0]), len(t[0]), len(xs if c else make()))  # tpyc: ok


def make() -> Own[list[int32]]:
    return [7]


# mutating the element a binding iterates warns, as `for v in ll[0]` does;
# each loop stops right after the write, so the iterator is not advanced
def warn_loop() -> None:
    ll: list[list[int32]] = [[1, 2], [3, 4]]
    for i, v in enumerate(ll[0]):
        ll[0].append(v + 10)  # tpyc: warning(/Mutation of 'll\[...\]' while iterating over it/)
        break
    for a, b in zip(ll[0], ll[1]):
        ll[1].append(a)  # tpyc: warning(/Mutation of 'll\[...\]' may hit the element being iterated/)
        break
    for v in iter(ll[0]):
        ll[0].append(v)  # tpyc: warning(/Mutation of 'll\[...\]' while iterating over it/)
        break
    # a false positive: reversed re-reads by index
    # (BUGS.md#reversed-element-growth-false-positive)
    for v in reversed(ll[0]):
        ll[0].append(v)  # tpyc: warning(/Mutation of 'll\[...\]' while iterating over it/)
        break
    d: dict[str, list[int32]] = {"k": [5, 6]}
    for i, v in enumerate(d["k"]):
        d["k"].append(v)  # tpyc: warning(/Mutation of 'd\[...\]' while iterating over it/)
        break
    print("warn-loop", ll, d)


# generator body: the same mutation inside a frame
def warn_gen(ll: list[list[int32]]) -> Iterator[int32]:
    for i, v in enumerate(ll[0]):
        ll[0].append(v)  # tpyc: warning(/Mutation of 'll\[...\]' while iterating over it/)
        break
    yield len(ll[0])


# a stored lazy binding keeps its loan on the element; neither is driven after
def warn_stored() -> None:
    ll: list[list[int32]] = [[1, 2], [3, 4]]
    e = enumerate(ll[0])
    ll[0].append(7)  # tpyc: warning(/Mutation of 'll\[...\]' while borrowed/)
    z = zip(ll[0], ll[1])
    ll[1].append(8)  # tpyc: warning(/Mutation of 'll\[...\]' may hit a borrowed element/)
    # a borrowing call's result keeps the element it returns; growth moves
    # only its buffer (BUGS.md#call-returned-container-growth-warns)
    y = pick(ll[1])
    ll[1].append(9)  # tpyc: warning(/Mutation of 'll\[...\]' while borrowed/)
    print("warn-stored", ll, y)


# two subscript hops: the loan is on the root's element the path leaves
# through, so replacing or growing that element warns; neither is driven
def warn_two_hop() -> None:
    lll = [[[1, 2]], [[3]]]
    e = iter(lll[0][0])
    lll[0] = [[9]]  # tpyc: warning(/Mutation of 'lll\[...\]' while borrowed/)
    z = zip(lll[1][0], lll[1][0])
    lll[1].append([5])  # tpyc: warning(/Mutation of 'lll\[...\]' while borrowed/)
    # a sibling element is not the one lent; growing the root is
    m = [[[1, 2]], [[3]]]
    e2 = iter(m[0][0])
    m[1].append([9])  # tpyc: ok
    m.append([[9]])  # tpyc: warning(/Mutation of 'm' while borrowed/)
    # a variable index may name the lent element
    n = [[[1, 2]], [[3]]]
    i = 0
    e3 = iter(n[i][0])
    n[0] = [[9]]  # tpyc: warning(/Mutation of 'n\[...\]' may hit a borrowed element/)
    print("warn-two-hop", lll, m, n)


class SH:
    s: set[int32]

    def __init__(self) -> None:
        self.s = {3, 1}


# set and dict-key objects: set element and field, dict keys of a name and
# of a dict element
def sets_and_keys() -> None:
    ss: list[set[int32]] = [{2, 1}]
    sh = SH()
    print("set", sum(ss[0]), max(ss[0]), sorted(ss[0]), len(ss[0]),  # tpyc: ok
          sorted(sh.s), sum(sh.s), min(sh.s))  # tpyc: ok
    dk = {"y": 1, "x": 2}
    dd: dict[str, dict[str, int32]] = {"k": {"b": 1, "a": 2}}
    print("dict-keys", sorted(dk), sorted(dd["k"]),  # tpyc: ok
          list(enumerate(dd["k"])), max(dd["k"]))  # tpyc: ok


# a user iterable record holding a pointer: its field is not an element the
# binding keeps, and a write to the record shows through a lazy binding
class Bag:
    xs: list[int32]
    p: Ptr[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]
        self.p = Ptr[int32]()

    def __iter__(self) -> Iterator[int32]:
        for x in self.xs:
            yield x


# a user iterable yielding views: the binding copies each view out, and a
# write to the record still shows through it; a loop drives it, as list()
# of views is BUGS.md#collect-iterator-of-views-into-dead-frame
class Words:
    ws: list[str]

    def __init__(self) -> None:
        self.ws = ["a", "b"]

    def __iter__(self) -> Iterator[StrView]:
        for w in self.ws:
            yield w


def user_iterable() -> None:
    bags = [Bag()]
    print("user-iterable", sum(bags[0]), sorted(bags[0]),  # tpyc: ok
          list(enumerate(bags[0])))  # tpyc: ok
    e = enumerate(bags[0])  # tpyc: ok
    bags[0].xs[0] = 10
    print("user-iterable-write", list(e))
    words = [Words()]
    for i, v in enumerate(words[0]):  # tpyc: ok
        words[0].ws[1] = "z"
        print("user-iterable-views", i, v)


# reference elements: each binding hands out the element itself, so a write
# through the loop variable or the result lands in the list, as in CPython;
# sorted() is left out, it copies (BUGS.md#sorted-ref-element-copy-unwarned)
class Row:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"Row({self.n})"

    def __lt__(self, o: Row) -> bool:
        return self.n < o.n


class RH:
    rs: list[Row]

    def __init__(self) -> None:
        self.rs = [Row(3), Row(1)]


def bump(r: Row) -> Row:
    r.n += 100
    return r


def big(r: Row) -> bool:
    return r.n > 1


def grow_rows(xs: list[Row]) -> None:
    xs.append(Row(0))


def ref_elements() -> None:
    ll = [[Row(5), Row(4)], [Row(6)]]
    h = RH()
    d = {"k": [Row(7), Row(8)]}
    for i, r in enumerate(ll[0]):  # tpyc: ok
        r.n += 10 + i
    for a, b in zip(ll[0], h.rs):  # tpyc: ok
        a.n += b.n
    for r in iter(d["k"]):  # tpyc: ok
        r.n += 1
    for r in reversed(h.rs):  # tpyc: ok
        r.n += 1
    for r in map(bump, ll[1]):  # tpyc: ok
        r.n += 1
    for r in filter(big, d["k"]):  # tpyc: ok
        r.n += 1000
    m = max(ll[0])  # tpyc: ok
    m.n += 1
    min(h.rs).n += 2  # tpyc: ok
    print("ref-elements", ll[0], ll[1], h.rs, d["k"])
    # a list parameter takes the element in place: the append lands in ll
    grow_rows(ll[0])  # tpyc: ok
    grow_rows(d["k"])  # tpyc: ok
    print("ref-elements-list-param", len(ll[0]), len(ll[1]), len(d["k"]))


# generator body: the loop variable is the element; its field read is copied
# to a local before the yield (BUGS.md#ephemeral-value-read-escape)
def ref_gen(ll: list[list[Row]]) -> Iterator[int32]:
    for i, r in enumerate(ll[0]):  # tpyc: ok
        r.n += 1
        n = r.n
        yield n


def main() -> None:
    folds()
    lazy()
    joins()
    write_through()
    H().m()
    LL = [[5, 6], [7]]
    print("ctor", C(LL).total)
    print("generator", list(gen(LL)))
    print("async", asyncio.run(co([[1, 2], [3, 4]])))
    comprehension()
    print("closure", closure())
    qualified()
    ptr_param()
    ba = bytearray(b"xyz")
    other_objects((ba, 1))
    warn_loop()
    warn_stored()
    print("warn-gen", list(warn_gen([[1, 2], [3]])))
    warn_two_hop()
    sets_and_keys()
    user_iterable()
    ref_elements()
    RL = [[Row(1), Row(2)]]
    print("ref-gen", list(ref_gen(RL)), RL[0])


main()
# module level: element reads of a global
LG = [[5, 6], [7]]
print("module", sum(LG[0]), sorted(LG[0]), list(enumerate(LG[1])), len(LG[1]))  # tpyc: ok
