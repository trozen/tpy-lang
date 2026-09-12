# The bytes family (bytes / bytearray / BytesView) compares, orders, hashes and
# looks up through ONE C++ view type, so a param, a view, a literal and an
# owned buffer meet with bare operators the way str / String / StrView do.
from typing import Iterator
from tpy import BytesView, Comparable, int32, Own


class Entry:
    name: bytes
    tags: set[bytes]

    def __init__(self, name: bytes) -> None:
        self.name = name
        self.tags = {b"a", b"b"}

    # method: a bytes field against a bytes param and a literal
    def is_named(self, other: bytes) -> bool:
        return self.name == other or self.name == b"default"  # tpyc: ok

    # method: a bytes needle against a set field
    def has_tag(self, tag: bytes) -> bool:
        return tag in self.tags  # tpyc: ok

    # user __contains__ with a bytes parameter: a literal needle renders the
    # static span into its `::tpy::BytesView` slot
    def __contains__(self, tag: bytes) -> bool:
        return tag in self.tags or tag == self.name  # tpyc: ok


class Bag:
    # user __contains__ whose slot OWNS the argument: a literal needle keeps
    # the owned render, since the view does not convert to the owner
    def __contains__(self, value: Own[bytes]) -> bool:
        return len(value) == 3  # tpyc: ok


class Box[T]:
    # generic __contains__: the open-T slot is the instantiation's parameter
    # form, the view at bytes, so a literal needle takes the static span
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item

    def __contains__(self, needle: T) -> bool:
        return needle == self.item  # tpyc: ok


# free function: every pairing of the family under == / != / < / <= / > / >=;
# the bytearray is mutated after the compares so the caller sees the alias
def compare_pairs(a: bytes, v: BytesView, ba: bytearray) -> None:
    print("pairs", a == v, v == a, a == ba, ba == a, a != b"zz", b"zz" != v)  # tpyc: ok
    print("order", a < b"b", b"b" > a, v <= a, ba >= v, a < ba)  # tpyc: ok
    ba.append(0x21)
    print("mutated", a == ba, ba > a)  # tpyc: ok


# free function: a bytes needle in a tuple literal, a list, a set and a dict
def membership(name: bytes, xs: list[bytes], s: set[bytes], d: dict[bytes, int32]) -> None:
    print("tuple", name in (b"PLAYPAL", b"COLORMAP"), name not in (b"PLAYPAL", b"COLORMAP"))  # tpyc: ok
    print("list", name in xs, b"zz" in xs)  # tpyc: ok
    print("set", name in s, name not in s, b"zz" in s)  # tpyc: ok
    print("dict", name in d, b"zz" not in d, name in d.keys())  # tpyc: ok


# free function: a view local (a slice) as compare operand and needle
def view_local(data: bytes) -> None:
    x = data[1:]  # tpyc: type(BytesView)
    print("view", x == b"bc", x in (b"bc", b"zz"), x < data)  # tpyc: ok


# free function: ordering makes list[bytes] sortable
def sort_bytes(xs: list[bytes]) -> None:
    xs.sort()  # tpyc: ok
    print("sort", xs)


# free function: a view hashes like the owner it reads; the empty view
def hashes(a: bytes) -> None:
    v = a[0:]  # tpyc: type(BytesView)
    print("hash", hash(v) == hash(a), hash(b"") == hash(bytes()), b"" == a[len(a):])  # tpyc: ok


# generator: the compare and the tuple chain inside a resumable frame
def matching(names: list[bytes], want: bytes) -> Iterator[bytes]:
    for n in names:
        if n == want or n in (b"PLAYPAL", b"COLORMAP"):  # tpyc: ok
            yield n


# generic: an open-T compare and ordering instantiated at bytes, where the
# parameter form (the view) meets the element's storage form (the owner)
def first_is[T: Comparable](xs: list[T], v: T) -> bool:
    return xs[0] == v or xs[0] < v  # tpyc: ok


# comprehension: filter by a bytes compare and a tuple chain
def filtered(names: list[bytes]) -> None:
    kept = [n for n in names if n != b"skip" and n in (b"a", b"b")]  # tpyc: ok
    print("comp", kept)


def main() -> None:
    e = Entry(b"x")
    print("method", e.is_named(b"x"), e.is_named(b"y"), e.has_tag(b"a"), e.has_tag(b"c"))
    print("contains", b"a" in e, b"x" in e, b"q" not in e)  # tpyc: ok
    bag = Bag()
    print("own_slot", b"abc" in bag, b"zz" in bag)  # tpyc: ok
    box = Box(b"abc")
    sbox = Box("abc")
    print("generic_slot", b"abc" in box, b"zz" in box, "abc" in sbox)  # tpyc: ok
    ba = bytearray(b"a")
    compare_pairs(b"a", b"a", ba)
    print("alias", ba)
    membership(b"COLORMAP", [b"COLORMAP", b"x"], {b"COLORMAP"}, {b"COLORMAP": 1})
    view_local(b"abc")
    sort_bytes([b"c", b"a", b"b"])
    hashes(b"hello")
    for m in matching([b"a", b"COLORMAP", b"b"], b"b"):
        print("gen", m)
    filtered([b"a", b"skip", b"c"])
    print("generic", first_is([b"b", b"c"], b"b"), first_is([b"b"], b"a"), first_is(["x"], "y"))


main()
