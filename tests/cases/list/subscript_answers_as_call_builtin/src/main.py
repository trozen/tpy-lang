# A builtin subscript (read, store, del, augmented store) answers as its
# explicit dunder call at every position, and the explicit calls agree with it.
import asyncio
from typing import Callable, Iterator
from tpy import (int8, int32, int64, uint8, uint16, Array, Span, StrView, String, BytesView,
                 readonly, error_return, ReturnException, copy, Own)


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


class Q:
    def __init__(self, w: int32) -> None:
        self.w = w


def wide() -> int64:
    return 5000000000


def fresh_list() -> Own[list[int32]]:
    return [1, 2, 3]


def fresh_dict() -> Own[dict[str, int32]]:
    return {"a": 1, "b": 2}


def ops(tag: str, xs: list[int32], d: dict[str, int32]) -> None:
    # The four subscript forms on a list and a dict.
    r = xs[0] + d["a"]  # tpyc: ok
    xs[1] = 20
    d["c"] = 30
    xs[2] += 5
    d["a"] += 7
    del xs[0]
    del d["b"]
    print(tag, r, xs, d)


def free_fn() -> None:
    xs = fresh_list()
    d = fresh_dict()
    ops("free", xs, d)


class Holder:
    xs: list[int32]
    d: dict[str, int32]

    def __init__(self) -> None:
        # Constructor position.
        self.xs = [1, 2, 3]
        self.d = {"a": 1, "b": 2}
        self.xs[0] = self.xs[1] + self.d["a"]  # tpyc: ok
        self.d["a"] += self.xs[0]
        del self.xs[2]
        print("ctor", self.xs, self.d)

    def run(self) -> None:
        # Method position, through `self`'s fields.
        self.xs[1] += 1  # tpyc: ok
        self.d["z"] = self.xs[0]
        del self.d["a"]
        print("method", self.xs[0], self.xs, self.d)


def gen(xs: list[int32], d: dict[str, int32]) -> Iterator[int32]:
    # Generator position: each form across a suspension.
    yield xs[0] + d["a"]  # tpyc: ok
    xs[1] = 9
    d["a"] += 1
    yield xs[1] + d["a"]
    del xs[0]
    del d["b"]
    yield len(xs) + len(d)


async def coro(xs: list[int32], d: dict[str, int32]) -> int32:
    # Async position.
    await asyncio.sleep(0)
    xs[0] += 10  # tpyc: ok
    d["a"] = xs[0]
    del xs[1]
    await asyncio.sleep(0)
    del d["b"]
    return xs[0] + d["a"]


class TooLong(Exception, ReturnException):
    pass


@error_return(TooLong)
def checked(xs: list[int32], d: dict[str, int32]) -> int32:
    # @error_return position.
    xs[0] = d["a"] + 1  # tpyc: ok
    d["b"] += xs[0]
    del xs[2]
    del d["a"]
    if len(xs) > 5:
        raise TooLong
    return xs[0] + d["b"]


def comp_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    # Comprehension position.
    print("comp", [xs[i] + d["a"] for i in range(2)])  # tpyc: bounds_checked(xs)


def closure_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()

    # Closure position.
    def bump() -> None:
        xs[0] += 100  # tpyc: ok
        d["a"] = xs[0]
        del xs[2]
        del d["b"]

    bump()
    print("closure", xs, d)


def with_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    # Context-manager body position.
    with open("subscript_case.txt", "w") as f:
        xs[1] = d["b"] * 3  # tpyc: ok
        d["b"] += 1
        del xs[0]
        del d["a"]
        f.write("x")
    print("with", xs, d)


def try_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    # try / finally position.
    try:
        xs[0] += d["a"]  # tpyc: ok
        del xs[1]
    finally:
        d["a"] = xs[0]
        del d["b"]
    print("try", xs, d)


def error_return_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    try:
        print("error_return", checked(xs, d), xs, d)
    except TooLong:
        print("error_return too long")


def match_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    # match arm position.
    match xs[0]:
        case 1:
            xs[0] = d["b"]  # tpyc: ok
            d["b"] += 2
            del xs[2]
            del d["a"]
        case _:
            pass
    print("match", xs, d)


def gen_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    print("gen", list(gen(xs, d)), xs, d)


def async_pos() -> None:
    xs = fresh_list()
    d = fresh_dict()
    print("async", asyncio.run(coro(xs, d)), xs, d)


def positions() -> None:
    free_fn()
    h = Holder()
    h.run()
    gen_pos()
    async_pos()
    comp_pos()
    closure_pos()
    with_pos()
    try_pos()
    error_return_pos()
    match_pos()


def keys() -> None:
    xs = [10, 20, 30, 40]
    # (Grown, so a list: BUGS.md#slice-assign-array-demoted-local.)
    xs.append(50)
    i: int32 = 2
    big: int = 1
    # Literal, negative, int32 and BigInt (checked narrow) keys.
    print("keys", xs[0], xs[-1], xs[i], xs[big])  # tpyc: bounds_checked(xs)
    # Narrower fixed-width keys convert to the int32 index as call arguments
    # do; a BigInt key takes the checked narrow.
    i8: int8 = 1
    u8: uint8 = 2
    u16: uint16 = 3
    huge: int = 4
    print("widths", xs[i8], xs[u8], xs[u16], xs[huge])  # tpyc: ok
    # A position proven in range keeps the unchecked element access.
    j = 0
    while j < len(xs):
        xs[j] = xs[j] + 1  # tpyc: bounds_safe(xs)
        j += 1
    print("bounds", xs)
    # Slices: written, stepped, and a slice-typed value.
    s = slice(0, 4, 2)
    print("slices", xs[1:3], xs[::2], xs[s])
    xs[1:3] = [7, 8, 9]
    print("slice store", xs)


def receivers(*args: int32) -> None:
    st = "hello"
    bs = b"bytes"
    ba = bytearray(b"abc")
    arr: Array[int32, 3] = [1, 2, 3]
    sp: Span[int32] = arr
    sv: StrView = "view"
    sg: String = String("owned")
    bv: BytesView = bs
    ba[0] = 65
    # A bytearray slice store.
    ba[0:2] = b"xy"  # tpyc: ok
    arr[2] += 4
    sp[0] = 9
    # str / bytes / bytearray / Array / Span / StrView / *args receivers.
    print("recv", st[1], st[1:3], bs[0], bs[1:3], ba[0], ba, arr[2], sp[0],
          sv[0], sv[1:], args[0], args[2])
    # String and BytesView receivers.
    print("recv views", sg[0], sg[1:], bv[0])  # tpyc: ok
    proven(st, bs, ba, arr, sv, sg, bv, args[0], args[1])


def proven(st: str, bs: bytes, ba: bytearray, arr: Array[int32, 3],
           sv: StrView, sg: String, bv: BytesView, *args: int32) -> None:
    # A position proven in range renders each receiver's unchecked twin,
    # read and store alike.
    sp: Span[int32] = arr
    vowels = 0
    for k in range(len(st)):
        if st[k] == "e":  # tpyc: bounds_safe(st)
            vowels += 1
    for k in range(len(sv)):
        if sv[k] == "e":  # tpyc: bounds_safe(sv)
            vowels += 1
    for k in range(len(sg)):
        if sg[k] == "e":  # tpyc: bounds_safe(sg)
            vowels += 1
    n = 0
    for k in range(len(bs)):
        n += bs[k]  # tpyc: bounds_safe(bs)
    for k in range(len(bv)):
        n += bv[k]  # tpyc: bounds_safe(bv)
    for k in range(len(args)):
        n += args[k]  # tpyc: bounds_safe(args)
    for k in range(len(ba)):
        ba[k] = 66  # tpyc: bounds_safe(ba)
    for k in range(len(arr)):
        arr[k] = k  # tpyc: bounds_safe(arr)
    for k in range(len(sp)):
        sp[k] = sp[k] + 1  # tpyc: bounds_safe(sp)
    print("proven", vowels, n, ba, arr[0], arr[2])


def bump(p: P) -> None:
    p.v += 1


def bump_proven(ps: list[P]) -> None:
    i = 0
    while i < len(ps):
        # A proven element passed to a mutating parameter: the mutable
        # variant's twin, so the write lands in the list's own element.
        bump(ps[i])  # tpyc: bounds_safe(ps)
        i += 1


def proven_mutation() -> None:
    ps = [P(1), P(2)]
    bump_proven(ps)
    print("proven bump", ps[0].v, ps[1].v)


def own_first(b: Own[bytearray]) -> Own[bytearray]:
    # An `Own[bytearray]` parameter read by index.
    print("own bytearray", b[0])  # tpyc: ok
    return b


def first_ro(xs: readonly[list[P]], d: readonly[dict[str, list[int32]]]) -> int32:
    # A readonly receiver: element, slice and dict value read.
    head = xs[0]
    part = xs[0:1]
    return head.v + len(part) + d["k"][0]


def opt_reads(xs: list[int32] | None, d: dict[str, int32] | None) -> int32:
    # An Optional receiver, checked at the access.
    return xs[0] + d["a"]  # tpyc: warning(/None/) warning(/None/)


def generic_store[T](xs: list[T], v: T) -> T:
    # A generic `list[T]`.
    xs[0] = copy(v)
    return xs[1]


def references() -> None:
    ps = [P(1), P(2)]
    # A reference element read, bound, then mutated: the list sees it.
    p = ps[0]
    p.v = 9
    print("ref read", ps[0].v)
    d: dict[str, list[int32]] = {"a": [1]}
    l = d["a"]
    l.append(2)
    print("ref dict read", d)
    # A reference element stored, then mutated through the container: the
    # store moves the last use in, so the element is that object.
    q = P(5)
    ps[1] = q
    ps[1].v = 6
    # A copy stored is the copy's own object.
    r = P(7)
    ps[0] = copy(r)
    r.v = 8
    print("ref store", ps[1].v, ps[0].v, r.v)


counter = [0]


def recv(xs: list[int32]) -> list[int32]:
    counter[0] += 1
    print("recv called")
    return xs


def k() -> int32:
    print("key called")
    return 0


def v() -> int32:
    print("value called")
    return 5


def side_effects() -> None:
    xs = [1, 2]
    # Each part evaluated once; order pinned under
    # BUGS.md#subexpression-right-to-left-eval.
    # (A plain store through a call receiver is not lowered yet:
    # BUGS.md#container-call-receiver-store-rejects.)
    recv(xs)[k()] += v()
    print("effects", xs, counter[0])


def nested(flag: bool) -> None:
    m = [[1, 2], [3]]
    a = [1]
    b = [2]
    # A nested element receiver, a ternary receiver.
    m[0][1] += 5
    a[0] = (a if flag else b)[0] + 6
    print("nested", m, a, b)


def pending() -> None:
    # Container literals: reads and stores leave the element open.
    ys = [1, 2]
    first = ys[0]
    ys.append(5000000000)  # tpyc: warning(/outside default int32 range/)
    zs = [1, 2]
    zs[0] = 5000000000  # tpyc: warning(/outside default int32 range/)
    e = {}
    e["k"] = 4
    keys_ = [0]
    ws = [10, 20]
    print("pending", first, ys, zs, e["k"], ws[keys_[0]])
    rep = [0] * 3
    rep[1] = 4
    print("repeat", rep[1], rep)
    # A delete takes an index, no element: the literal stays open.
    ds = [1, 2, 3]
    del ds[0]
    ds.append(5000000000)  # tpyc: warning(/outside default int32 range/)
    print("pending del", ds)


class Grid:
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: tuple[int32, int32]) -> int32:
        return k[0] * 10 + k[1]


class ByP:
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: P) -> int32:
        return k.v * 2


class Named:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __getitem__(self, k: str) -> int32:
        return self.n + len(k)


def record_keys(flag: bool, a: Named, b: Named) -> None:
    g = Grid()
    q = ByP()
    p = P(4)
    gs = [Named(1)]
    # A record read takes the key and receiver its explicit call does: a
    # tuple key, a record key, an element receiver, a ternary receiver.
    print("record keys", g[1, 2], q[p], gs[0]["ab"], (a if flag else b)["t"])


def explicit_calls() -> None:
    # The explicit dunder calls accept what the subscripts do.
    xs = [1, 2]
    a = xs.__getitem__(0)
    xs.append(5000000000)  # tpyc: warning(/outside default int32 range/)
    zs = [1, 2]
    zs.__setitem__(0, 5000000000)  # tpyc: warning(/outside default int32 range/)
    fs: list[Callable[[int32], int32]] = [lambda x: x]
    fs.__setitem__(0, lambda x: x + 1)
    fs[0] = lambda x: x + 2
    ds: list[dict[str, int64]] = [{}]
    ds.__setitem__(0, {"a": 1})
    ds[0] = {"b": 2}
    ts: list[tuple[int64, float]] = [(1, 2.0)]
    ts.__setitem__(0, (3, 4))
    ts[0] = (5, 6.0)
    # A tuple widening a container literal's open element, stored both ways.
    tw = [(1, 2)]
    tw.__setitem__(0, (wide(), 5))  # tpyc: ok
    tw[0] = (wide(), 6)  # tpyc: ok
    print("explicit", a, xs, zs, fs[0](1), ds, ts, tw)


def union_store() -> None:
    us: list[P | Q] = [Q(1), Q(2)]
    # A member of the element union is a value the explicit call takes, as
    # the store does. (`us[0] = P(5)` passes sema and is not lowered yet:
    # BUGS.md#union-element-member-store-rejects.)
    us.__setitem__(1, P(6))  # tpyc: ok
    b = us[1]
    if isinstance(b, P):
        print("union store", b.v)


def popped_keys() -> None:
    xs = [10, 20, 30, 40]
    ks: list[int32] = [0, 1, 2, 3]
    # A key with a side effect is evaluated once per subscript: read, store,
    # and an augmented store that reads and writes one element.
    r = xs[ks.pop()]  # tpyc: ok
    xs[ks.pop()] = 7  # tpyc: ok
    xs[ks.pop()] += 1  # tpyc: ok
    print("popped", r, xs, ks)


# Module-level position: a global list and dict.
GXS: list[int32] = [1, 2, 3]
GD: dict[str, int32] = {"a": 1, "b": 2}
GXS[0] = GXS[1] + GD["a"]  # tpyc: ok
GD["a"] += GXS[0]
GXS[2] += 1
# (`del GXS[1]` here is not lowered yet: BUGS.md#module-level-del-item-rejects.)
print("module", GXS, GD)


def main() -> None:
    positions()
    keys()
    receivers(4, 5, 6)
    ba = bytearray(b"ab")
    back = own_first(ba)
    print("own back", back)
    print("readonly", first_ro([P(3)], {"k": [4]}))
    print("optional", opt_reads([1], {"a": 2}))
    print("generic", generic_store([1, 2], 5))
    references()
    side_effects()
    nested(True)
    pending()
    record_keys(False, Named(1), Named(5))
    explicit_calls()
    union_store()
    popped_keys()
    proven_mutation()


main()
