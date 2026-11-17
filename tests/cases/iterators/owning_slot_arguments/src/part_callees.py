# A method's and a constructor's owning element slot: the same rule as a free
# callee's; a free callee's arguments whose container keeps its elements; and
# a set / dict handed over to a plain, a generator and a comprehension callee.
from typing import Iterable, Iterator
from tpy import Array, Own, int32, nocopy


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    items: list[R]

    def __init__(self, xs: Iterable[Own[R]]) -> None:
        self.items = []
        for x in xs:
            self.items.append(x)

    def add(self, xs: Iterable[Own[R]]) -> None:
        for x in xs:
            self.items.append(x)


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def take(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        x.v += 100
        t += x.v
    return t


def mk_rows() -> Own[list[list[R]]]:
    return [[R(1)], [R(2)]]


class Rows(list[R]):
    pass


def make_rows() -> Own[Rows]:
    xs = Rows()
    xs.append(R(1))
    return xs


class Key:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __eq__(self, o: "Key") -> bool:
        return self.v == o.v

    def __hash__(self) -> int:
        return self.v


@nocopy
class V:
    def __init__(self, v: int32) -> None:
        self.v = v


def keep_keys(xs: Iterable[Own[Key]]) -> Own[list[Key]]:
    out: list[Key] = []
    for x in xs:
        out.append(x)
    return out


def gen_keys(xs: Iterable[Own[Key]]) -> Iterator[Key]:
    for x in xs:
        x.v += 100
        yield x


def comp_keys(xs: Iterable[Own[Key]]) -> Own[list[Key]]:
    return [x for x in xs]


def mk_dict() -> Own[dict[Key, V]]:
    return {Key(1): V(10), Key(2): V(20)}


def set_dict_parts() -> None:
    # A dead set is handed over: no copy warning, the call renders
    # `std::move(s)`, and what the callee kept is its own to mutate.
    s = {Key(1), Key(2)}
    ks = keep_keys(s)  # tpyc: ok
    ks[0].v = 50
    print("set plain", [k.v for k in ks])
    s2 = {Key(3), Key(4)}
    print("set gen", [k.v for k in gen_keys(s2)])  # tpyc: ok
    s3 = {Key(5), Key(6)}
    ks3 = comp_keys(s3)  # tpyc: ok
    ks3[1].v = 60
    print("set comprehension", [k.v for k in ks3])
    # A fresh dict is handed over as the value the call returns: no copy
    # warning, and its `@nocopy` values make a whole-dict copy uncompilable.
    print("dict plain", [k.v for k in keep_keys(mk_dict())])  # tpyc: ok
    print("dict gen", [k.v for k in gen_keys(mk_dict())])  # tpyc: ok


def main() -> None:
    set_dict_parts()
    ll = [[R(1)], [R(2)]]
    h = Holder(ll[0])  # tpyc: warning(/copies R elements/)
    h.items[0].v = 9
    print("ctor element", h.items[0].v, len(ll[0]))
    h2 = Holder(mk())  # tpyc: ok
    print("ctor fresh", len(h2.items))
    h3 = Holder([R(1)])  # tpyc: ok
    print("ctor literal", len(h3.items))
    d = [R(5)]
    h3.add(d)  # tpyc: ok
    h3.items[1].v = 7
    print("method dead", h3.items[1].v)
    live = [R(5)]
    h3.add(live)  # tpyc: warning(/copies R elements/)
    h3.items[2].v = 7
    print("method live", h3.items[2].v, len(live))
    h3.add([R(i) for i in range(2)])  # tpyc: ok
    print("method comprehension", len(h3.items))
    # An Array at its last use is handed over through its consuming
    # `__iter__`: no element is copied.
    a = Array[R, 2]([R(1), R(2)])
    print("array last use", take(a))  # tpyc: ok
    # A comprehension clause variable over a fresh list refers into it: lent.
    # `own_iter(mk_rows())` avoids the copy (part_consuming).
    print("clause", [take(r) for r in mk_rows()])  # tpyc: warning(/copies R elements/)
    # A fresh subclass of list has no consuming iteration of its own, so it
    # is lent and its elements are copied, as a named one's are
    # (BUGS.md#dead-native-like-source-own-slot-copies).
    print("fresh subclass", take(make_rows()))  # tpyc: warning(/copies R elements/)
