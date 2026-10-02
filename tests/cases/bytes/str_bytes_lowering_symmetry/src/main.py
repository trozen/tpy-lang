# `str` and `bytes` lower alike: each section pairs a `str` shape with its
# `bytes` twin at one lowering site.
from typing import Any
from tpy import Own, Span


class Tagged:
    name: str
    tag: bytes
    age: int

    def __init__(self, name: str, tag: bytes, age: int) -> None:
        self.name = name
        self.tag = tag
        self.age = age


class StrMap:
    d: dict[str, str]

    def __init__(self) -> None:
        self.d = {}

    def __getitem__(self, k: str) -> str:
        return self.d[k]

    def __setitem__(self, k: str, v: str) -> None:
        self.d[k] = v


class BytesMap:
    d: dict[bytes, bytes]

    def __init__(self) -> None:
        self.d = {}

    def __getitem__(self, k: bytes) -> bytes:
        return self.d[k]

    def __setitem__(self, k: bytes, v: bytes) -> None:
        self.d[k] = v


def concat_literal(s: str, b: bytes) -> None:
    # A literal operand of `+` / `+=` lands in the operator's view param, so
    # the bytes literal stays static like the str one.
    x = s + "!"  # tpyc: ok
    y = b + b"!"  # tpyc: ok
    x += "?"  # tpyc: ok
    y += b"?"  # tpyc: ok
    print("concat_literal:", x, "<" + s, y, b"<" + b)


def aug_setitem() -> None:
    # A concat aug-assign on a container element.
    xs = ["a"]
    ys = [b"a"]
    xs[0] += "b"  # tpyc: ok
    ys[0] += b"b"  # tpyc: ok
    ds = {"k": "v"}
    db = {b"k": b"v"}
    ds["k"] += "w"  # tpyc: ok
    db[b"k"] += b"w"  # tpyc: ok
    print("aug_setitem:", xs, ys, ds, db)


def set_methods(s: str, b: bytes) -> None:
    # The set method surface over an owned element: literal, field, view
    # param and concat inserts, then removal and the set algebra.
    t = Tagged("n", b"t", 1)
    ss: set[str] = set()
    sb: set[bytes] = set()
    ss.add("a")  # tpyc: ok
    sb.add(b"a")  # tpyc: ok
    ss.add(t.name)  # tpyc: ok
    sb.add(t.tag)  # tpyc: ok
    ss.add(s)
    sb.add(b)
    ss.add(s + "!")
    sb.add(b + b"!")
    ss.discard("a")  # tpyc: ok
    sb.discard(b"a")  # tpyc: ok
    ss.remove("n")  # tpyc: ok
    sb.remove(b"t")  # tpyc: ok
    us = ss.union({"z"})
    ub = sb.union({b"z"})
    print("set_methods:", sorted(ss), sorted(sb), len(us), len(ub),
          "z" in us, b"z" in ub)


def union_elem() -> None:
    # A field read and a literal at a value-union element slot.
    t = Tagged("n", b"t", 1)
    ds: dict[str, str | int] = {"name": t.name, "age": t.age}  # tpyc: ok
    db: dict[str, bytes | int] = {"tag": t.tag, "age": t.age}  # tpyc: ok
    ls: list[str | int] = ["two", 1]
    lb: list[bytes | int] = [b"two", 1]  # tpyc: ok
    print("union_elem:", ds, db, len(ls), len(lb))


def owned_slot_arg() -> None:
    # Owned element reads and an owned rvalue into an `Own[...]` insert slot.
    ts = ("a", 1)
    tb = (b"a", 1)
    xs = ["p", "q"]
    xb = [b"p", b"q"]
    outs: list[str] = []
    outb: list[bytes] = []
    outs.append(ts[0])  # tpyc: ok
    outb.append(tb[0])  # tpyc: ok
    outs.append(xs[1])  # tpyc: ok
    outb.append(xb[1])  # tpyc: ok
    outs.append(xs[0] + "!")  # tpyc: ok
    outb.append(xb[0] + b"!")  # tpyc: ok
    xs.append("r")
    xb.append(b"r")
    print("owned_slot_arg:", outs, outb, len(xs), len(xb))


def tuple_decl() -> None:
    # Container elements of a tuple literal decl.
    ts = ({"a"}, ["b", "c"], {"k": 1}, 1)  # tpyc: ok
    tb = ({b"a"}, [b"b", b"c"], {b"k": 1}, 1)  # tpyc: ok
    print("tuple_decl:", ts[3], tb[3])


def second[T](p: tuple[T, str]) -> str:
    return p[1]  # tpyc: ok


def second_b[T](p: tuple[T, bytes]) -> bytes:
    return p[1]  # tpyc: ok


def open_sibling_tuple() -> None:
    # A tuple element read next to an open type-param element.
    print("open_sibling_tuple:", second((1, "x")), second_b((1, b"x")))


def first_s(xs: Span[str]) -> str:
    return xs[0]  # tpyc: ok


def first_b(xs: Span[bytes]) -> bytes:
    return xs[0]  # tpyc: ok


def span_elem() -> None:
    # An element read off a span.
    xs = ["a", "b"]
    xb = [b"a", b"b"]
    print("span_elem:", first_s(xs), first_b(xb))


def comprehensions() -> None:
    # A comprehension unpack target and a comprehension dict key.
    ps = [("a", 1), ("b", 2)]
    pb = [(b"a", 1), (b"b", 2)]
    ks = [k for k, v in ps]  # tpyc: ok
    kb = [k for k, v in pb]  # tpyc: ok
    ds = {"x": "y"}
    db = {b"x": b"y"}
    vs = [v for k, v in ds.items()]  # tpyc: ok
    vb = [v for k, v in db.items()]  # tpyc: ok
    ms = {k: 1 for k in ks}  # tpyc: ok
    mb = {k: 1 for k in kb}  # tpyc: ok
    print("comprehensions:", ks, kb, vs, vb, ms, mb)


def items_loop() -> None:
    # A for-each unpack over a dict's items view.
    ds = {"x": "y"}
    db = {b"x": b"y"}
    for k, v in ds.items():  # tpyc: ok
        print("items_loop:", k, v)
    for k, v in db.items():  # tpyc: ok
        print("items_loop:", k, v)


def any_value_dict() -> None:
    # A dict with an `Any` value: write and delete by key.
    ds: dict[str, Any] = {}
    db: dict[bytes, Any] = {}
    ds["a"] = 1  # tpyc: ok
    db[b"a"] = 1  # tpyc: ok
    ds["b"] = 2
    db[b"b"] = 2
    del ds["a"]  # tpyc: ok
    del db[b"a"]  # tpyc: ok
    print("any_value_dict:", len(ds), len(db))


def tuple_elem_recv() -> None:
    # A container member of a list's tuple element, mutated in place.
    items: list[tuple[str, list[int]]] = [("a", [1])]  # tpyc: ok
    itemb: list[tuple[bytes, list[int]]] = [(b"a", [1])]  # tpyc: ok
    items[0][1].append(5)  # tpyc: ok
    itemb[0][1].append(5)  # tpyc: ok
    print("tuple_elem_recv:", items, itemb)


def record_subscript() -> None:
    # A user record's `__getitem__` / `__setitem__` keyed by the view family.
    ms = StrMap()
    mb = BytesMap()
    ps = ("k", "v")
    pb = (b"k", b"v")
    ms[ps[0]] = ps[1]  # tpyc: ok
    mb[pb[0]] = pb[1]  # tpyc: ok
    ms["a"] = "b"  # tpyc: ok
    mb[b"a"] = b"b"  # tpyc: ok
    ms["c"] = ps[1] + "!"  # tpyc: ok
    mb[b"c"] = pb[1] + b"!"  # tpyc: ok
    print("record_subscript:", ms["a"], mb[b"a"], ms.d, mb.d)  # tpyc: ok


def main() -> None:
    concat_literal("s", b"b")
    aug_setitem()
    set_methods("s", b"b")
    union_elem()
    owned_slot_arg()
    tuple_decl()
    open_sibling_tuple()
    span_elem()
    comprehensions()
    items_loop()
    any_value_dict()
    tuple_elem_recv()
    record_subscript()


main()
