# A record subscript t[k], t[k] = v, del t[k] and t[k] op= v answers like the
# explicit t.__getitem__(k) / __setitem__ / __delitem__ call, at every position
# (variants winner-first, so the CPython dispatch stub runs the same ones); an
# augmented target evaluates once each part whose repetition would show (a
# call into a body, a user dunder, a writing or iterator-advancing builtin, a
# walrus, a property or user getter).
import asyncio
from typing import Callable, Iterator, Self
from tpy import (Own, ReturnException, StrView, auto_readonly, dispatch,
                 error_return, int8, int32, int64, nocopy, readonly)


class Table:
    nums: list[int32]
    names: dict[str, int32]
    log: list[str]

    def __init__(self) -> None:
        self.nums = [0, 0, 0, 0]
        self.names = {}
        self.log = []

    @dispatch
    def __getitem__(self, i: int32) -> int32:
        return self.nums[i]

    @dispatch
    def __getitem__(self, k: str) -> int32:
        return self.names[k]

    @dispatch
    def __setitem__(self, i: int32, v: int32) -> None:
        self.nums[i] = v

    @dispatch
    def __setitem__(self, k: str, v: int32) -> None:
        self.names[k] = v

    @dispatch
    def __delitem__(self, i: int32) -> None:
        self.log.append("del-int")

    @dispatch
    def __delitem__(self, k: str) -> None:
        self.log.append("del-" + k)


class Sub(Table):
    def __init__(self) -> None:
        super().__init__()


class Box[T]:
    items: list[T]

    def __init__(self, a: Own[T], b: Own[T]) -> None:
        self.items = [a, b]

    @dispatch
    def __getitem__(self, i: int32) -> T:
        return self.items[i]

    @dispatch
    def __getitem__(self, k: str) -> T:
        return self.items[len(k) - 1]


class Sink:
    seen: list[str]

    def __init__(self) -> None:
        self.seen = []

    def __setitem__(self, k: str, v: int32) -> None:
        self.seen.append(k + "=" + str(v))


class Wide:
    def __init__(self) -> None:
        pass

    @dispatch
    def __getitem__(self, k: int32) -> str:
        return "int32"

    @dispatch
    def __getitem__(self, k: int) -> str:
        return "int"


class Rows:
    data: dict[str, list[int32]]

    def __init__(self) -> None:
        self.data = {"a": [1]}

    @dispatch
    def __getitem__(self, k: str) -> list[int32]:
        return self.data[k]

    @dispatch
    def __getitem__(self, i: int32) -> list[int32]:
        return self.data["a"]


class Keys:
    calls: int32

    def __init__(self) -> None:
        self.calls = 0

    def key(self) -> int32:
        self.calls += 1
        return 1

    def name(self) -> str:
        self.calls += 1
        return "n"

    def big(self) -> int:
        self.calls += 1
        return 1099511627776


class RoGet:
    vals: list[int32]
    hits: int32

    def __init__(self) -> None:
        self.vals = [1, 2]
        self.hits = 0

    @dispatch
    @readonly(False)
    def __getitem__(self, k: str) -> int32:
        self.hits += 1
        return 0

    @dispatch
    @readonly
    def __getitem__(self, k: int32) -> int32:
        return self.vals[k]

    def __setitem__(self, k: int32, v: int32) -> None:
        self.vals[k] = v


class Fallback:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def m(self, k: str) -> int32:
        self.hits += 1
        return 200

    @dispatch
    @readonly
    def m(self, k: int32) -> int32:
        return 101


class Pair:
    data: list[int32]

    def __init__(self) -> None:
        self.data = [1]

    @auto_readonly
    def __getitem__(self, k: int32) -> auto_readonly[list[int32]]:
        return self.data


class Bag:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def __contains__(self, k: str) -> bool:
        self.hits += 1
        return True

    @dispatch
    @readonly
    def __contains__(self, k: int32) -> bool:
        return k > 0


class Stages:
    def __init__(self) -> None:
        pass

    @dispatch
    @readonly(False)
    def __getitem__(self, k: int32) -> int32:
        return 32

    @dispatch
    @readonly
    def __getitem__(self, k: int) -> int32:
        return 64


class OwnKeys:
    m: dict[str, int32]

    def __init__(self) -> None:
        self.m = {"a-key-longer-than-the-small-string-buffer": 1, "v": 1}

    def __getitem__(self, k: str) -> int32:
        return self.m[k]

    def __setitem__(self, k: Own[str], v: int32) -> None:
        self.m[k] = v


class Rec:
    x: int32

    def __init__(self) -> None:
        self.x = 1


class Props:
    _r: Rec
    i: int32

    def __init__(self) -> None:
        self._r = Rec()
        self.i = 1

    @property
    def rec(self) -> Rec:
        print("property: rec getter")
        return self._r

    def rec_m(self) -> Rec:
        print("property: rec_m call")
        return self._r

    @property
    def index(self) -> int32:
        print("property: index getter")
        return self.i


class Grid:
    rows: list[list[int32]]
    calls: int32

    def __init__(self) -> None:
        self.rows = [[1], [2]]
        self.calls = 0

    @readonly(False)
    def __getitem__(self, i: int32) -> list[int32]:
        self.calls += 1
        return self.rows[i]


class Val:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: Val) -> int:
    print("borrow_call: key_of")
    return p.v


def wrap(p: Val) -> Val:
    return p


HELD = Rec()


def held_rec() -> Rec:
    print("attribute: held_rec")
    return HELD


def long_key() -> str:
    return "a-key-longer-than-the-small-string-buffer"


def view(s: str) -> str:
    print("view_key: view")
    return s.strip()


def trace(msg: str, v: int32) -> int32:
    print(msg)
    return v


def label() -> str:
    return " v "


def pick(t: Table) -> Table:
    print("call_receiver: pick")
    return t


@nocopy
class Token:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def use(self: Own[Self]) -> int32:
        return self.n + 1


def make_token(ks: Keys) -> Own[Token]:
    ks.calls += 1
    return Token(1)


def spend(tk: Own[Token]) -> int32:
    return tk.n


def tail(s: StrView) -> StrView:
    return s[1:]


def strip_view(s: str) -> StrView:
    return s.strip()


def spell(ks: Keys, s: str) -> str:
    ks.calls += 1
    return " " + s + "-a-key-longer-than-the-small-string-buffer "


def none_written(names: list[str]) -> int32:
    return 1


class Stock:
    vals: list[int32]

    def __init__(self) -> None:
        self.vals = [1, 2]


class Shelf:
    calls: int32
    stock: Stock

    def __init__(self) -> None:
        self.calls = 0
        self.stock = Stock()

    def get(self) -> Stock:
        self.calls += 1
        return self.stock


TAKEN: list[Stock] = []


def take(st: Own[Stock]) -> int32:
    n = len(st.vals) - 1
    TAKEN.append(st)
    return n


class Reader:
    def __add__(self, tk: Own[Token]) -> int32:
        return tk.n


def peek(tk: Token) -> int32:
    tk.n += 1
    return tk.n - 2


def peek_opt(tk: Token | None) -> int32:
    if tk is None:
        return 0
    tk.n += 1
    return tk.n - 2


def fresh_keys(ks: Keys) -> Own[list[int32]]:
    ks.calls += 1
    return [1]


def first(xs: list[int32]) -> int32:
    xs.append(5)
    return xs[0]


LOG: list[str] = []


class V:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __neg__(self) -> int32:
        LOG.append("neg")
        return 1

    def __repr__(self) -> str:
        LOG.append("repr")
        return "r"

    def __lt__(self, o: Self) -> bool:
        LOG.append("lt")
        return self.tag < o.tag


def counted(ks: Keys) -> Iterator[int32]:
    ks.calls += 1
    yield 1
    ks.calls += 1
    yield 0


HITS: list[int32] = []


def hit() -> int32:
    HITS.append(1)
    return 1


def apply(f: Callable[[], int32]) -> int32:
    return f()


class Q:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class PQ:
    v: int
    q: Q

    def __init__(self, v: int) -> None:
        self.v = v
        self.q = Q(v)

    def get_q(self) -> Q:
        return self.q


async def akey(ks: Keys) -> int32:
    await asyncio.sleep(0)
    ks.calls += 1
    return 1


class AsyncRec:
    x: int32

    def __init__(self) -> None:
        self.x = 0


async def awaited_keys(t: Table, ks: Keys) -> int32:
    # async: an awaited key is evaluated once, before the statement
    xs = [0, 0]
    xs[await akey(ks)] += 1  # tpyc: ok
    d = {1: 0}
    d[await akey(ks)] += 1  # tpyc: ok
    rs = [AsyncRec(), AsyncRec()]
    rs[await akey(ks)].x += 1  # tpyc: ok
    # async: a held key call beside an awaited right-hand side
    t[ks.key()] += await akey(ks)  # tpyc: ok
    print("async_key:", xs, d, rs[1].x)
    return t[1]


class KeyedHolder:
    t: Table

    def __init__(self, ks: Keys) -> None:
        self.t = Table()
        # constructor: a held key call
        self.t[ks.key()] += 3  # tpyc: ok

    def bump(self, ks: Keys) -> int32:
        # method: a held key call
        self.t[ks.key()] += 2  # tpyc: ok
        return self.t[1]


def closure_key(t: Table, ks: Keys) -> int32:
    def inner() -> None:
        # closure: a held key call
        t[ks.key()] += 1  # tpyc: ok
    inner()
    return t[1]


def match_key(t: Table, ks: Keys, k: str) -> int32:
    match k:
        case "a":
            # match arm: a held key call
            t[ks.key()] += 1  # tpyc: ok
            return t[1]
        case _:
            return 0


def read_pair(p: readonly[Pair]) -> None:
    print("pair_readonly:", p[0][0], len(p.__getitem__(0)))  # tpyc: ok


class Holder:
    t: Table

    def __init__(self) -> None:
        self.t = Table()

    def bump(self) -> int32:
        # method: a field receiver
        self.t["m"] = 3  # tpyc: ok
        return self.t["m"] + self.t[0]  # tpyc: ok


class Seeded:
    t: Table
    first: int32

    def __init__(self) -> None:
        self.t = Table()
        # constructor body
        self.t["c"] = 4  # tpyc: ok
        self.first = self.t["c"]  # tpyc: ok


class Scope:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Failed(Exception, ReturnException):
    pass


def gen(t: Table, ks: Keys) -> Iterator[int32]:
    # generator: store, read, del and aug inside the resumable frame
    t["y"] = 1  # tpyc: ok
    yield t["y"]  # tpyc: ok
    t["y"] += 1  # tpyc: ok
    yield t["y"]
    # generator: the aug key evaluated once
    t[ks.key()] += 1  # tpyc: ok
    yield t[1]
    del t["y"]  # tpyc: ok
    yield len(t.log)


async def later(t: Table, ks: Keys) -> int32:
    await asyncio.sleep(0)
    # async: the same forms inside the async frame
    t["z"] = 2  # tpyc: ok
    t["z"] += 3  # tpyc: ok
    # async: the aug key evaluated once
    t[ks.key()] += 3  # tpyc: ok
    del t[2]  # tpyc: ok
    return t["z"] + t[1]  # tpyc: ok


@error_return(Failed)
def checked(t: Table) -> int32:
    # @error_return body
    t["e"] = 7  # tpyc: ok
    if t["e"] == 0:
        raise Failed()
    return t["e"]


@error_return(Failed)
def checked_key(t: Table, ks: Keys) -> int32:
    # @error_return body: a held key call
    t[ks.key()] += 6  # tpyc: ok
    if t[1] == 0:
        raise Failed()
    return t[1]


def by_match(t: Table, k: str) -> int32:
    match k:
        case "a":
            # match arm
            return t[k]  # tpyc: ok
        case _:
            return t[1]


def closure(t: Table) -> int32:
    def inner(k: str) -> int32:
        # closure
        return t[k]  # tpyc: ok
    return inner("a")


def main() -> None:
    t = Table()
    # free: a str key beside an int key, each form resolved as its explicit call
    t["a"] = 10  # tpyc: ok
    t[1] = 20  # tpyc: ok
    print("free:", t["a"], t[1], t.__getitem__("a"), t.__getitem__(1))  # tpyc: ok
    t["a"] += 5  # tpyc: ok
    t[1] -= 2  # tpyc: ok
    del t["a"]  # tpyc: ok
    del t[0]  # tpyc: ok
    print("free:", t.names, t.nums, t.log)

    # inherited: the parent's sets answer the child's subscripts
    s = Sub()
    s["x"] = 5  # tpyc: ok
    del s["x"]  # tpyc: ok
    print("inherited:", s.names, s[0], s.log)  # tpyc: ok

    # generic: the receiver's type args substituted, then ranked
    b = Box(7, 8)
    print("generic:", b[0], b["ab"], b.__getitem__("a"))  # tpyc: ok

    # setter_only: a store needs no getter
    snk = Sink()
    snk["k"] = 1  # tpyc: ok
    print("setter_only:", snk.seen)

    # keys: converted at the variant ranked, as the call converts them
    w = Wide()
    n8: int8 = 3
    n64: int64 = 1099511627776
    big: int = 1099511627776
    print("keys:", w[n8], w[1], w[n64], w[big])  # tpyc: ok

    # reference: a reference-returning getter aliases the stored list
    r = Rows()
    r["a"].append(2)  # tpyc: ok
    row = r[0]  # tpyc: ok
    row.append(3)
    print("reference:", len(r["a"]), r.data["a"])

    # once: an augmented subscript evaluates its key once, the builtin sibling too
    ks = Keys()
    t[ks.key()] += 5  # tpyc: ok
    xs = [1, 2, 3]
    xs[ks.key()] += 1  # tpyc: ok
    d = {"n": 1}
    d[ks.name()] += 1  # tpyc: ok
    print("once:", t[1], xs, d, ks.calls)

    h = Holder()
    print("method:", h.bump())
    print("ctor:", Seeded().first)

    g = Table()
    gk = Keys()
    print("generator:", list(gen(g, gk)), g.log, gk.calls)
    ak = Keys()
    print("async:", asyncio.run(later(Table(), ak)), ak.calls)

    # pop: a key from a writing builtin is popped once (`bytearray.pop()`
    # stands in for `indices.pop()`: BUGS.md#own-int-subscript-index-rejects)
    pops = bytearray(b"\x00\x01")
    xs[pops.pop()] += 10  # tpyc: ok
    print("pop:", xs, len(pops))

    # walrus: a walrus key rebinds once
    w_i = 0
    xs[(w_i := w_i + 1)] += 100  # tpyc: ok
    print("walrus:", xs, w_i)

    # receiver: a receiver that runs code is evaluated once, before the key
    rows = [[1, 2], [3, 4]]
    rows[trace("receiver: i", 0)][trace("receiver: k", 1)] += 5  # tpyc: ok
    print("receiver:", rows)
    # call_receiver: a call returning the record it was passed, evaluated once
    ct = Table()
    pick(ct)[trace("call_receiver: key", 1)] += 4  # tpyc: ok
    print("call_receiver:", ct.nums)

    # view_key: a key viewing a temporary is held with the temporary
    vt = Table()
    vd = {"v": 1}
    vt[label().strip()] = 1
    vt[label().strip()] += 1  # tpyc: ok
    vt[label()[1:2]] += 1  # tpyc: ok
    vd[label().strip()] += 1  # tpyc: ok
    # view_key: an outer call over a held call is held over it
    vt[view(label())] += 1  # tpyc: ok
    print("view_key:", vt.names, vd)

    # own_key: a held key the setter takes by value moves in after the
    # update is held
    ok = OwnKeys()
    ok[long_key()] += 1  # tpyc: ok
    # own_key: a view key over a held call converts at the setter
    ok[label().strip()] += 1  # tpyc: ok
    print("own_key:", ok.m)

    # attribute: a call receiver returning stored state, evaluated once
    held_rec().x += 1  # tpyc: ok
    print("attribute:", HELD.x)

    # property: a property receiver and a property index run their getter once
    pp = Props()
    pp.rec.x += 1  # tpyc: ok
    pp.rec_m().x += 1  # tpyc: ok
    xs[pp.index] += 1000  # tpyc: ok
    print("property:", pp._r.x, xs)

    # user_getter: a user `__getitem__` under the target runs once
    gr = Grid()
    gr[0][0] += 1  # tpyc: ok
    print("user_getter:", gr.rows, gr.calls)

    # borrow_call: a borrow-returning call in the target is evaluated once
    va = Val(1)
    vb = Val(2)
    max(va, vb, key=key_of).v += 5  # tpyc: ok
    vs = [Val(1), Val(2)]
    wrap(vs[0]).v += 10  # tpyc: ok
    vdd = {"a": Val(1)}
    vfb = Val(2)
    vdd.get("a", vfb).v += 1  # tpyc: ok
    print("borrow_call:", va.v, vb.v, vs[0].v, vs[1].v, vdd["a"].v)

    # readonly_getter: the getter's readonly variant passes the receiver
    # const, the setter takes it as it is
    rg = RoGet()
    rk = Keys()
    rg[rk.key()] += 2  # tpyc: ok
    print("readonly_getter:", rg.vals, rg.hits, rk.calls)

    # fallback: no mutating variant applies, so the readonly one is ranked
    fb = Fallback()
    print("fallback:", fb.m(1), fb.hits)  # tpyc: ok

    # pair: the mutable @auto_readonly face on a mutable receiver aliases
    pr = Pair()
    pr[0].append(2)  # tpyc: ok
    pr.__getitem__(0).append(3)
    print("pair:", len(pr.data))
    read_pair(pr)

    # contains: `in` ranks its set with the receiver as the call does
    bg = Bag()
    print("contains:", 1 in bg, bg.__contains__(1), "s" in bg, bg.hits)  # tpyc: ok

    # stages: a preferred variant converting the key wins over an exact
    # fallback
    sg = Stages()
    big5: int = 5
    print("stages:", sg[big5], sg.__getitem__(big5))  # tpyc: ok

    # optional: a narrowed Optional receiver answers as its record
    ot: Table | None = Table()
    if ot is not None:
        ot["q"] = 2  # tpyc: ok
        print("optional:", ot["q"])  # tpyc: ok

    c = Table()
    c["a"] = 11
    print("comprehension:", [c[k] for k in ["a", "a"]], [c[i] for i in range(2)])  # tpyc: ok
    print("closure:", closure(c))
    print("match:", by_match(c, "a"), by_match(c, "b"))

    with Scope() as one:
        # with body
        c["w"] = one  # tpyc: ok
        print("with:", c["w"])  # tpyc: ok
    fks = Keys()
    try:
        c["f"] = 9  # tpyc: ok
    finally:
        # finally
        c[fks.key()] += 1  # tpyc: ok
        print("finally:", c["f"], c[1], fks.calls)  # tpyc: ok
    try:
        print("error_return:", checked(c))
    except Failed:
        print("error_return: failed")

    positions()
    regions()
    backings()
    consumers()
    selection()
    facts()


def positions() -> None:
    pk = Keys()
    print("method:", KeyedHolder(Keys()).bump(pk), pk.calls)
    ck = Keys()
    kh = KeyedHolder(ck)
    print("ctor:", kh.t[1], ck.calls)
    lk = Keys()
    print("closure_key:", closure_key(Table(), lk), lk.calls)
    mk = Keys()
    print("match_key:", match_key(Table(), mk, "a"), mk.calls)
    ek = Keys()
    try:
        print("error_return_key:", checked_key(Table(), ek), ek.calls)
    except Failed:
        print("error_return_key: failed")
    wt = Table()
    wk = Keys()
    with Scope() as one:
        # with body: a held key call
        wt[wk.key()] += one  # tpyc: ok
    print("with_key:", wt[1], wk.calls)
    # bigint_key: an `int` key returned by a call is held once
    bk = Keys()
    bd: dict[int, int32] = {1099511627776: 1}
    bd[bk.big()] += 1  # tpyc: ok
    print("bigint_key:", bd, bk.calls)
    # wrapped_borrow: a borrow-declared call behind a method hop and in an
    # index runs once
    pqs = [PQ(1), PQ(2)]
    pit = iter(pqs)
    pd = PQ(0)
    next(pit, pd).get_q().v += 10  # tpyc: ok
    vals = [0, 0, 0]
    vals[next(pit, pd).v] += 5  # tpyc: ok
    print("wrapped_borrow:", pqs[0].q.v, pqs[1].v, vals)
    ak = Keys()
    av = asyncio.run(awaited_keys(Table(), ak))
    print("async_total:", av, ak.calls)


def regions() -> None:
    rt = Table()
    rk = Keys()
    flag = rk.calls == 0
    xs = [0, 0, 0, 0]
    # region_ternary: only the arm taken runs; the ternary is held whole
    rt[rk.key() if flag else rk.key()] += 1  # tpyc: ok
    xs[rk.key() if flag else rk.key()] += 1  # tpyc: ok
    print("region_ternary:", rt.nums, xs, rk.calls)
    # region_nested: the outer ternary is held whole
    rt[(rk.key() if flag else 0) if flag else rk.key()] += 1  # tpyc: ok
    xs[(rk.key() if flag else 0) if flag else rk.key()] += 1  # tpyc: ok
    print("region_nested:", rt.nums, xs, rk.calls)
    # region_comprehension: the comprehension runs its calls once per element
    rt[sum([rk.key() for i in range(3)])] += 1  # tpyc: ok
    xs[sum([rk.key() for i in range(3)])] += 1  # tpyc: ok
    print("region_comprehension:", rt.nums, xs, rk.calls)
    # region_chained: a later comparison runs only when the earlier holds
    rt[int(rk.key() > 5 > rk.key())] += 1  # tpyc: ok
    xs[int(rk.key() > 5 > rk.key())] += 1  # tpyc: ok
    print("region_chained:", rt.nums, xs, rk.calls)
    # consumed: a new value a held reader takes by ownership moves in
    rt[spend(make_token(rk))] += 1  # tpyc: ok
    xs[spend(make_token(rk))] += 1  # tpyc: ok
    print("consumed:", rt.nums, xs, rk.calls)
    long_y = spell(rk, "y")
    d = {long_y: 0}
    # region_or: an `or` over new strings is held whole
    d[spell(rk, "y") or spell(rk, "n")] += 1  # tpyc: ok
    print("region_or:", d[long_y], rk.calls)
    # region_and: an `and` over new strings
    d[spell(rk, "n") and spell(rk, "y")] += 1  # tpyc: ok
    print("region_and:", d[long_y], rk.calls)
    # region_or_scalar: an `or` over a call
    xs[rk.key() or 0] += 1  # tpyc: ok
    print("region_or_scalar:", xs, rk.calls)
    # region_genexpr: a generator expression runs its calls once per element
    xs[sum(rk.key() for i in range(3))] += 1  # tpyc: ok
    print("region_genexpr:", xs, rk.calls)
    # region_lambda: the call inside a lambda runs once
    xs[apply(lambda: hit())] += 1  # tpyc: ok
    print("region_lambda:", xs, len(HITS))
    # region_fresh_key: a ternary of new strings is the key itself
    d[spell(rk, "y") if flag else spell(rk, "n")] += 1  # tpyc: ok
    print("region_fresh_key:", d[long_y], rk.calls)
    # region_inert_choice: choosing a list of records runs nothing, so only
    # the builtin call is held
    ka = [Keys()]
    kb = [Keys(), Keys()]
    xs[len(ka if flag else kb)] += 1  # tpyc: ok
    print("region_inert_choice:", xs)
    # region_inert_arg: a record chosen and passed runs nothing; the call
    # into a body is held, and its write to the token shows
    t1 = Token(1)
    t2 = Token(5)
    xs[peek_opt(t1 if flag else t2)] += 1  # tpyc: ok
    print("region_inert_arg:", xs, t1.n, t2.n)


def backings() -> None:
    s = "  a-key-longer-than-the-small-string-buffer"
    bt = Table()
    bt.names[s[2:]] = 0
    d: dict[str, int32] = {}
    d[s[2:]] = 0
    # backing: a held view is held after the temporary it points into
    bt[tail(" " + s[2:])] += 5  # tpyc: ok
    d[tail(" " + s[2:])] += 5  # tpyc: ok
    print("backing:", bt.names, d)
    bk = Keys()
    flag = bk.calls == 0
    long_y = "y-a-key-longer-than-the-small-string-buffer"
    bt.names[long_y] = 0
    d[long_y] = 0
    # backing_region: a view over a ternary of new values holds the ternary
    bt[strip_view(spell(bk, "y") if flag else spell(bk, "n"))] += 1  # tpyc: ok
    d[strip_view(spell(bk, "y") if flag else spell(bk, "n"))] += 1  # tpyc: ok
    print("backing_region:", bt.names[long_y], d[long_y], bk.calls)
    names = [" k ", " j "]
    bt.names["k"] = 0
    d["k"] = 0
    # container_view: only the key call is held; the element view is re-read
    bt[names[bk.key() - 1].strip()] += none_written(names)  # tpyc: ok
    d[names[bk.key() - 1].strip()] += none_written(names)  # tpyc: ok
    print("container_view:", bt.names["k"], d["k"], bk.calls)


def consumers() -> None:
    xs = [0, 0, 0, 0]
    sh = Shelf()
    # consumed_copy: a borrow taken by ownership is copied, as warned (so the
    # shelf keeps its stock, which is all this section reads); a borrowed
    # result is not known to be new, so it is named first
    st = sh.get()
    xs[take(st)] += 1  # tpyc: warning(/copies Stock into owned storage/)
    print("consumed_copy:", xs, len(sh.stock.vals), sh.calls)
    ck = Keys()
    # consumed_receiver: a new value a consuming method takes moves in
    xs[make_token(ck).use()] += 1  # tpyc: ok
    print("consumed_receiver:", xs, ck.calls)
    # consumed_operand: a new value an operator takes by ownership moves in
    xs[Reader() + make_token(ck)] += 1  # tpyc: ok
    print("consumed_operand:", xs, ck.calls)
    # borrowed_nocopy: a held @nocopy value is read in place by a borrowing call
    xs[peek(make_token(ck))] += 1  # tpyc: ok
    print("borrowed_nocopy:", xs, ck.calls)
    # borrowed_list: a held new list is read in place, not copied
    xs[first(fresh_keys(ck))] += 1  # tpyc: ok
    print("borrowed_list:", xs, ck.calls)
    # tuple_key: the tuple around a held call is built once
    d3 = {(spell(ck, "t"),): 0}
    d3[(spell(ck, "t"),)] += 1  # tpyc: ok
    print("tuple_key:", d3, ck.calls)
    # tuple_arg: a held tuple passed to a function is built once
    xs[tuple_first((spell(ck, "u"),))] += 1  # tpyc: ok
    print("tuple_arg:", xs, ck.calls)


def selection() -> None:
    a = V("a-long-tag-beyond-the-small-string-buffer")
    b = V("b-long-tag-beyond-the-small-string-buffer")
    xs = [0, 0, 0, 0]
    # dunder_unary: a user `__neg__` under the key runs once
    xs[-a] += 1  # tpyc: ok
    print("dunder_unary:", xs, LOG)
    LOG.clear()
    # dunder_fstring: a user `__repr__` in an f-string key runs once
    rd = {"r": 0}
    rd[f"{a!r}"] += 1  # tpyc: ok
    print("dunder_fstring:", rd, LOG)
    LOG.clear()
    # dunder_builtin: `sorted` comparing with a user `__lt__` runs once (the
    # copy is the list literal's own: it stores `a` by value, as it does
    # outside an augmented target)
    td = {a.tag: 0}
    td[sorted([b, a])[0].tag] += 1  # tpyc: warning(/copies V into owned storage/)
    print("dunder_builtin:", td[a.tag], LOG)
    LOG.clear()
    # iterator_key: a builtin advancing a named generator runs once
    gk = Keys()
    g = counted(gk)
    xs[sum(g)] += 5  # tpyc: ok
    print("iterator_key:", xs, gk.calls)


class HKey:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __hash__(self) -> int:
        LOG.append("hash")
        return self.n

    def __eq__(self, o: Self) -> bool:
        LOG.append("eq")
        return self.n == o.n


class EqV:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, o: Self) -> bool:
        LOG.append("eq")
        return self.n == o.n


class OwnGet:
    def __getitem__(self, k: Own[str]) -> int32:
        LOG.append("getitem")
        return 1


class Lender[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def get(self) -> T:
        return self.v


class Logged:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __del__(self) -> None:
        LOG.append(f"x={self.x}")


def tuple_first(t: tuple[str]) -> int32:
    return 0


def refill(d: dict[HKey, int32]) -> int32:
    d.clear()
    for i in range(100):
        d[HKey(i + 10)] = 7
    return 1


def refill_big(d: dict[HKey, int]) -> int32:
    d.clear()
    for i in range(100):
        d[HKey(i + 10)] = 7
    return 1


def stock_size(st: Stock) -> int32:
    return len(st.vals)


def ident[T](x: T) -> T:
    return x


def ident_s(x: str) -> str:
    return x


def opt_key(t: Token | None) -> int32:
    if t is None:
        return 0
    return t.n


def count_tokens(ts: list[Token]) -> int32:
    return len(ts)


def rank(s: str) -> int:
    return len(s)


def empty_label() -> str:
    return ""


def long_label() -> str:
    return "g-a-label-longer-than-the-small-string-buffer"


def facts() -> None:
    xs = [0, 0, 0, 0]
    # hash_key: a builtin subscript hashing a user key hashes it once
    hd = {HKey(1): 2}
    k = HKey(1)
    LOG.clear()
    xs[hd[k]] += 1  # tpyc: ok
    print("hash_key:", xs, LOG)
    # hash_settled: the held element is a number held by value once its
    # type settles, so a value that refills the dict leaves it intact
    hd2 = {HKey(1): 2}
    ys = [0, 0, 0, 0]
    ys[hd2[k]] += refill(hd2)  # tpyc: ok
    print("hash_settled:", ys)
    # hash_settled_int: an `int` element is a number held by value as well
    hb: dict[HKey, int] = {HKey(1): 2}
    zs = [0, 0, 0, 0]
    zs[hb[k]] += refill_big(hb)  # tpyc: ok
    print("hash_settled_int:", zs)
    # number_reader: a value not known to be new is held through the number
    # read off it, in one evaluation
    it = iter(["aa", "3"])
    xs[len(next(it, "a"))] += 1  # tpyc: ok
    xs[int(next(it, "0"))] += 1  # tpyc: ok
    sh = Shelf()
    xs[stock_size(sh.get())] += 1  # tpyc: ok
    print("number_reader:", xs, sh.calls)
    # generic_twin: a generic body returning its value parameter holds the
    # key as its monomorphic twin does
    gd = {"a": 0}
    gkey = "a"
    gd[ident(gkey)] += 1  # tpyc: ok
    gd[ident_s(gkey)] += 1  # tpyc: ok
    print("generic_twin:", gd)
    # hash_get: a held `get` with a literal default
    LOG.clear()
    ys[hd.get(k, 0)] += 1  # tpyc: ok
    print("hash_get:", ys, LOG)
    LOG.clear()
    # eq_in: `in` over elements with a user `__eq__` runs it once
    a = EqV(1)
    vs = [EqV(1)]
    ws = [EqV(1)]
    xs[int(a in vs)] += 1  # tpyc: ok
    print("eq_in:", xs, LOG)
    LOG.clear()
    # eq_list: `==` over lists of such elements runs it once
    xs[int(vs == ws)] += 1  # tpyc: ok
    print("eq_list:", xs, LOG)
    LOG.clear()
    # generator_in: `in` over a named generator advances it once
    gk = Keys()
    g = counted(gk)
    xs[int(1 in g)] += 1  # tpyc: ok
    print("generator_in:", xs, gk.calls)
    # optional_arg: a held new value reaches an Optional parameter converted
    tk = Keys()
    xs[opt_key(make_token(tk))] += 1  # tpyc: ok
    print("optional_arg:", xs, tk.calls)
    # nocopy_literal: a held list of a @nocopy element is read in place
    xs[count_tokens([make_token(tk)])] += 1  # tpyc: ok
    print("nocopy_literal:", xs, tk.calls)
    # owned_getitem: a user `__getitem__` taking its key by ownership moves
    # the new key in
    ob = OwnGet()
    xs[ob[spell(tk, "o")]] += 1  # tpyc: ok
    print("owned_getitem:", xs, tk.calls, LOG)
    LOG.clear()
    # in_place_key: a key `min(..., key=)` may hand back in place is named
    # first, and the Own setter does not move it out of the list
    names = ["a-key-longer-than-the-small-string-buffer",
             "a-key-longer-than-the-small-string-buffer-and-more"]
    ow = OwnKeys()
    mk = min(names[0], names[1], key=rank)
    ow[mk] += 1  # tpyc: ok
    print("in_place_key:", ow.m, names)
    # attr_temp: the field of a held call lending into a held new value is
    # written once (its destructor shows the value at the statement's end)
    Lender(Logged(7)).get().x += 1  # tpyc: ok
    print("attr_temp:", LOG)
    LOG.clear()
    # select_ref: a left operand handed back in place is copied, not moved out
    d = {1: long_label()}
    r1 = d.setdefault(1, "") or "dflt"  # tpyc: ok
    r2 = min(names[0], names[1], key=rank) or "z"  # tpyc: ok
    print("select_ref:", r1 == d[1], r2 == names[0], names)
    # select_local: a new left operand is moved out of its temp when chosen
    s = long_label() or "none"  # tpyc: ok
    print("select_local:", s)
    # select_chain: the outer select's left operand is a select, read in place
    x = empty_label() or long_label() or "z"  # tpyc: ok
    print("select_chain:", x)


main()

GT = Table()
GT.names["g"] = 6
# module level: a read through the global receiver's pointer slot (a store
# into a global record is not lowered yet: BUGS.md#global-record-subscript-store)
print("module:", GT["g"], GT[2])  # tpyc: ok
XS: list[int32] = [0, 0]
XK = Keys()
# module level: a held key call into a global builtin container
XS[XK.key()] += 1  # tpyc: ok
print("module_key:", XS, XK.calls)
