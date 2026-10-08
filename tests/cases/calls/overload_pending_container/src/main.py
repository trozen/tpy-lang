# An undecided container argument (an unannotated container local, or a
# literal written in the call) is scored against overload candidates without
# being decided; the least-widening applicable candidate wins and decides it.
# Every callee that takes a list / dict / set mutates it and the caller reads
# it back, so a silent copy shows. Ranking sections whose pick CPython's
# dispatch emulation cannot follow (declaration order) live in
# `calls/overload_pending_container_rank`.
import asyncio
from typing import Callable, Iterable, Iterator

from tpy import Array, Own, Span, dispatch, int32, int64


def a64() -> int64:
    return 1099511627776


# --- call path (a): user-function overloads, one group per container kind ---

@dispatch
def f_list(xs: list[int64]) -> str:
    xs.append(7)
    return "list[int64]"


@dispatch
def f_list(xs: str) -> str:
    return "str"


@dispatch
def f_dict(d: dict[str, int64]) -> str:
    d["z"] = 9
    return "dict[str, int64]"


@dispatch
def f_dict(d: str) -> str:
    return "str"


@dispatch
def f_set(s: set[int64]) -> str:
    s.add(9)
    return "set[int64]"


@dispatch
def f_set(s: str) -> str:
    return "str"


@dispatch
def f_rows(g: list[list[int64]]) -> str:
    g.append([5])
    return "list[list[int64]]"


@dispatch
def f_rows(g: str) -> str:
    return "str"


@dispatch
def f_pairs(ps: list[tuple[int64, float]]) -> str:
    ps.append((3, 0.5))
    return "list[tuple[int64, float]]"


@dispatch
def f_pairs(ps: str) -> str:
    return "str"


@dispatch
def f_floats(xs: list[float]) -> str:
    xs.append(1.5)
    return "list[float]"


@dispatch
def f_floats(xs: str) -> str:
    return "str"


@dispatch
def f_opt(xs: list[int64] | None) -> str:
    if xs is not None:
        xs.append(7)
    return "list[int64] | None"


@dispatch
def f_opt(xs: str) -> str:
    return "str"


@dispatch
def f_own(xs: Own[list[int64]]) -> Own[list[int64]]:
    xs.append(7)
    return xs


@dispatch
def f_own(xs: str) -> Own[list[int64]]:
    return []


@dispatch
def f_words(ws: list[str]) -> str:
    ws.append("c")
    return "list[str]"


@dispatch
def f_words(ws: str) -> str:
    return "str"


# --- written empty containers: `[]`, `list()`, `{}`, `set()` ---

@dispatch
def fill_list(xs: list[float]) -> int32:
    xs.append(1.5)
    return len(xs)


@dispatch
def fill_list(xs: str) -> int32:
    return -1


@dispatch
def fill_dict(d: dict[str, float]) -> int32:
    d["a"] = 1.5
    return len(d)


@dispatch
def fill_dict(d: str) -> int32:
    return -1


@dispatch
def fill_set(s: set[float]) -> int32:
    s.add(1.5)
    return len(s)


@dispatch
def fill_set(s: str) -> int32:
    return -1


# --- dict and set literals written in the call ---

@dispatch
def dict_rank(d: dict[str, int32]) -> str:
    return "32"


@dispatch
def dict_rank(d: dict[str, int64]) -> str:
    return "64"


@dispatch
def set_rank(s: set[int64]) -> str:
    return "64"


@dispatch
def set_rank(s: set[int32]) -> str:
    return "32"


@dispatch
def lit_rows(d: dict[str, list[int64]]) -> int64:
    return d["a"][1]


@dispatch
def lit_rows(d: str) -> int64:
    return 0


@dispatch
def lit_keys(d: dict[tuple[int64, int64], int64]) -> int32:
    return len(d)


@dispatch
def lit_keys(d: str) -> int32:
    return -1


# --- Span and Array parameters ---

@dispatch
def f_span(xs: Span[int64]) -> str:
    xs[0] = 1099511627776
    return "Span[int64]"


@dispatch
def f_span(xs: str) -> str:
    return "str"


@dispatch
def f_array(xs: Array[int64, 2]) -> str:
    xs[1] = 1099511627776
    return "Array[int64, 2]"


@dispatch
def f_array(xs: str) -> str:
    return "str"


# --- an Own parameter at a literal written in the call ---

@dispatch
def own_pick(xs: Own[list[int32]]) -> Own[list[int32]]:
    xs.append(3)
    return xs


@dispatch
def own_pick(xs: Own[list[int64]]) -> Own[list[int64]]:
    xs.append(1099511627776)
    return xs


# --- ranking groups: each variant names itself ---

@dispatch
def rank32(xs: list[int32]) -> str:
    xs.append(7)
    return "32"


@dispatch
def rank32(xs: list[int64]) -> str:
    xs.append(7)
    return "64"


@dispatch
def rank_wide(xs: list[int64]) -> str:
    xs.append(7)
    return "64"


@dispatch
def rank_wide(xs: list[int32]) -> str:
    xs.append(7)
    return "32"


@dispatch
def floor_a(xs: list[int64]) -> str:
    xs.append(7)
    return "int64"


@dispatch
def floor_a(xs: list[int]) -> str:
    xs.append(7)
    return "int"


@dispatch
def pair(xs: list[int32], n: int32) -> str:
    xs.append(n)
    return "32"


@dispatch
def pair(xs: list[int64], n: int64) -> str:
    xs.append(n)
    return "64"


@dispatch
def two(xs: list[int64], ys: list[int64]) -> str:
    xs.append(7)
    ys.append(8)
    return "64"


@dispatch
def two(xs: list[int32], ys: list[int32]) -> str:
    return "32"


@dispatch
def gen_or_str[T](xs: list[T], v: T) -> T:
    # Honest for any T: at a reference type the list holds a copy of `v`.
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return v


@dispatch
def gen_or_str(xs: str, v: str) -> str:
    return v


# A declared view decides nothing; the list candidate would widen the
# list, so the view wins (declared first, as CPython's dispatch tries it).
@dispatch
def view_first(n: int32, xs: Iterable[int64]) -> str:
    return "Iterable"


@dispatch
def view_first(n: int32, xs: list[int64]) -> str:
    return "list"


@dispatch
def view_first_big(n: int, xs: Iterable[int64]) -> str:
    return "Iterable"


@dispatch
def view_first_big(n: int, xs: list[int64]) -> str:
    return "list"


# --- one container at two positions ---

@dispatch
def joint_views(a: Iterable[int32], b: Iterable[int32]) -> str:
    return f"views {sum(a)} {sum(b)}"


@dispatch
def joint_views(a: list[int32], b: list[int64]) -> str:
    return "storage"


@dispatch
def joint_wide(a: list[int64], b: list[int64]) -> str:
    b.append(7)
    return "both int64"


@dispatch
def joint_wide(a: list[int32], b: list[int64]) -> str:
    return "split"


@dispatch
def gen_kw[T](xs: list[T], v: T) -> T:
    # Honest for any T: at a reference type the list holds a copy of `v`.
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return v


@dispatch
def gen_kw(xs: str) -> str:
    return xs


# --- locals whose cells settle together (`lb = la`) are wanted at one type ---

@dispatch
def linked(a: list[int64], b: list[int64]) -> str:
    a.append(1)
    b.append(2)
    return "both int64"


@dispatch
def linked(a: list[int32], b: list[int64]) -> str:
    a.append(1)
    return "split"


# --- one list under two names widens once, as the same name twice does ---

@dispatch
def two_names(a: list[int64], b: list[int64]) -> str:
    a.append(1)
    return "lists"


@dispatch
def two_names(a: list[int64], b: Iterable[int64]) -> str:
    a.append(1)
    return "view"


# --- an element stored from one list into another ties them one way ---

@dispatch
def one_way(a: list[int64], b: list[int64]) -> str:
    a.append(8)
    return "both int64"


@dispatch
def one_way(a: list[int64], b: list[int32]) -> str:
    a.append(7)
    return "split"


# --- the least widening, in declaration orders CPython's dispatch follows ---

# A view of int widens nothing a list of int64 would.
@dispatch
def from_view_big(xs: Iterable[int]) -> str:
    return "iterbig"


@dispatch
def from_view_big(xs: list[int64]) -> str:
    return "list64"


# The declared view takes the list as it is; the concrete candidate would
# widen it.
@dispatch
def view_first32(xs: Iterable[int32]) -> str:
    return "iter32"


@dispatch
def view_first32(xs: list[int64]) -> str:
    return "list64"


# A view of int64 converts each element; a list of int would widen it.
@dispatch
def view_first64(xs: Iterable[int64]) -> str:
    return "iter64"


@dispatch
def view_first64(xs: list[int]) -> str:
    return "big"


# A generic and its declared twin: the generic's resolved view would widen
# the list, the declared view decides nothing, in either order.
@dispatch
def twin_generic_first[T](xs: Iterable[T], v: T) -> str:
    return "generic"


@dispatch
def twin_generic_first(xs: Iterable[int64], v: int64) -> str:
    return "declared"


@dispatch
def twin_declared_first(xs: Iterable[int64], v: int64) -> str:
    return "declared"


@dispatch
def twin_declared_first[T](xs: Iterable[T], v: T) -> str:
    return "generic"


# A declared view is a declared slot in a generic candidate as in a plain
# one. `gv`'s generic cannot be instantiated at an int32 list (its view
# refuses the element, BUGS.md#generic-declared-view-refuses-conversion),
# so the list candidate is the one applicable.
@dispatch
def gv[T](xs: Iterable[int64], t: T) -> str:
    return "gen-view"


@dispatch
def gv(xs: list[int64], t: int32) -> str:
    return "list64"


@dispatch
def hv(xs: Iterable[int64], t: int32) -> str:
    return "plain-view"


@dispatch
def hv(xs: list[int64], t: int32) -> str:
    return "list64"


# Regime C: the same rank with a lambda beside the container.
@dispatch
def rc_rank(xs: Iterable[int32], f: Callable[[int32], int32]) -> str:
    return "iter32"


@dispatch
def rc_rank(xs: list[int64], f: Callable[[int64], int64]) -> str:
    return "list64"


# --- a nested call beside a container ---

@dispatch
def one(a: int32) -> int32:
    return a


@dispatch
def one(a: int32, b: int32) -> int32:
    return a + b


@dispatch
def outer(n: int32, xs: list[int64]) -> str:
    xs.append(7)
    return "list[int64]"


@dispatch
def outer(n: int32, xs: str) -> str:
    return "str"


# --- losing candidates leave nothing on the argument ---

@dispatch
def lose_array(xs: list[int32], n: int32) -> str:
    xs.append(n)
    return f"list[int32] {len(xs)}"


@dispatch
def lose_array(xs: Array[int64, 2], n: str) -> str:
    return "Array"


@dispatch
def lose_span(xs: list[int32], n: int32) -> str:
    xs.append(n)
    return "list[int32]"


@dispatch
def lose_span(xs: Span[int64], n: str) -> str:
    return "Span"


# --- Regime C: a lambda argument beside the container ---

@dispatch
def apply(xs: list[int64], f: Callable[[int64], int64]) -> int64:
    xs.append(7)
    return f(xs[0])


@dispatch
def apply(xs: str, f: Callable[[str], int32]) -> int32:
    return 0


# --- call paths (d) method group, (f) generic method / generic ctor, (g) dunder ---

class Acc:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    @dispatch
    def add(self, xs: list[int64]) -> str:
        xs.append(8)
        return "list[int64]"

    @dispatch
    def add(self, xs: str) -> str:
        return "str"

    @dispatch
    def fill(self, xs: list[float]) -> int32:
        xs.append(0.5)
        return len(xs)

    @dispatch
    def fill(self, xs: str) -> int32:
        return -1

    @dispatch
    def fill_map(self, d: dict[str, float]) -> int32:
        d["b"] = 0.5
        return len(d)

    @dispatch
    def fill_map(self, d: str) -> int32:
        return -1

    def push[T](self, xs: list[T], v: T) -> T:
        # Honest for any T: at a reference type the list holds a copy of `v`.
        xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
        return v

    # An operator's operand is read-only, so this callee reads the list.
    @dispatch
    def __add__(self, o: list[int64]) -> int32:
        return len(o)

    @dispatch
    def __add__(self, o: int32) -> int32:
        return o

    def in_method(self) -> None:
        # position: a method body
        ms = [1, 2]
        print("method:", f_list(ms), ms[2])  # the call decides ms


class Holder[T]:
    first: T

    def __init__(self, xs: list[T], v: T) -> None:
        # Honest for any T: at a reference type the list holds a copy of
        # `v`, and the field a copy of the list's first element.
        xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
        self.first = xs[0]  # tpyc: warning(/may copy T into field/)


def in_generator() -> Iterator[str]:
    # position: a generator body
    ms = [1, 2]
    yield f_list(ms)  # the call decides ms
    ms.append(a64())
    yield str(ms[3])


async def in_async() -> str:
    # position: an async body
    ms = [1, 2]
    r = f_list(ms)  # the call decides ms
    ms.append(a64())
    return f"{r} {ms[3]}"


@dispatch
def f_list32(xs: list[int32]) -> str:
    xs.append(7)
    return "list[int32]"


@dispatch
def f_list32(xs: str) -> str:
    return "str"


# position: module level -- a global is decided at its binding (int32 here),
# so only a candidate taking it as it is applies
MS = [1, 2]


def main() -> None:
    # list: the call decides the local as list[int64]; the callee's append
    # is visible to the caller, and a later wide value fits.
    ms = [1, 2]  # tpyc: type(list[int64])
    print("list:", f_list(ms), ms[2])
    ms.append(a64())
    print("list:", ms[3])

    # dict local
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    print("dict:", f_dict(d), d["z"])
    d["w"] = a64()
    print("dict:", d["w"])

    # set local
    s = {1, 2}  # tpyc: type(set[int64])
    print("set:", f_set(s), len(s))
    s.add(a64())
    print("set:", len(s))

    # nested rows
    g = [[1], [2]]  # tpyc: type(list[list[int64]])
    print("rows:", f_rows(g), len(g))
    g[0].append(a64())
    print("rows:", g[0][1])

    # tuple members
    ps = [(1, 2.0)]  # tpyc: type(list[tuple[int64, float]])
    print("tuples:", f_pairs(ps), len(ps))
    ps.append((a64(), 1.0))
    print("tuples:", ps[2][0])

    # empty list local: the winner seeds it
    es = []  # tpyc: type(list[float])
    print("empty local:", f_floats(es), es[0])
    es.append(2.5)
    print("empty local:", es)

    # empty dict and set locals: the winner seeds them
    ed = {}  # tpyc: type(dict[str, int64])
    print("empty dict local:", f_dict(ed), ed["z"])
    ed["w"] = a64()
    print("empty dict local:", ed["w"])
    eset = set()  # tpyc: type(set[int64])
    print("empty set local:", f_set(eset), len(eset))
    eset.add(a64())
    print("empty set local:", len(eset))

    # literals written in the call adapt to the winner
    print("inline:", f_list([1, 2]))
    print("inline empty:", f_floats([]))

    # the four spellings of a written empty container, at a function
    print("written empty:", fill_list([]), fill_list(list()), fill_dict({}),
          fill_set(set()))
    # ... and at a method group (`list()` / `set()` at its mutated slot
    # do not lower: BUGS.md#record-rvalue-at-mutated-method-slot-unhoisted)
    acc = Acc()
    print("written empty method:", acc.fill([]), acc.fill_map({}))
    # a literal written at a method group
    print("method literal:", acc.fill([1.5, 2.5]))

    # a dict or set literal written in the call is scored with its numbers
    # still literals, as a list literal is, and the winner's parameter
    # types it: an int64 value, a float one, a keyword argument, a method
    print("dict literal:", f_dict({"a": 1}), fill_dict({"k": 1}),
          f_dict(d={"a": 1}), acc.fill_map({"k": 1.5}))
    print("set literal:", f_set({1, 2}))
    # the least widening; a value too wide for int32 leaves only int64; a
    # typed value is its own type
    wide = a64()
    print("literal rank:", dict_rank({"a": 1}), set_rank({1, 5000000000}),
          set_rank({wide}))
    # a list literal value and tuple keys are written values too
    print("literal parts:", lit_rows({"a": [1, 2]}), lit_keys({(1, 2): 3}))
    # a candidate that reads the literal through a view or a type parameter
    # reads the type its own values give: it is typed at once
    print("literal view:", max({2.5, 1.5}), max({(1, "a"), (2, "b")}),
          min({2.5, 1.5}, key=lambda v: -v))
    # a list literal at a builtin method's Own slot renders typed as well
    sd: dict[int32, list[int32]] = {}
    print("own setdefault:", len(sd.setdefault(1, [5, 6])))

    # keyword argument
    ks = [1, 2]  # tpyc: type(list[int64])
    print("keyword:", f_list(xs=ks), ks[2])
    ks.append(a64())
    print("keyword:", ks[3])

    # Optional parameter
    op = [1, 2]  # tpyc: type(list[int64])
    print("optional:", f_opt(op), op[2])
    op.append(a64())
    print("optional:", op[3])

    # Own parameter: a literal written in the call, then a local
    print("own:", f_own([1, 2]))
    ol = [1, 2]  # tpyc: type(list[int64])
    print("own local:", f_own(ol))
    # Own[list[int32]] / Own[list[int64]] at a literal: the least widening
    print("own pick:", own_pick([1, 2]))
    # a typed prvalue: a bare `{0}` would also build the `str` overload's view
    print("own zero:", f_own([0]))

    # a non-numeric element
    ws = ["a", "b"]  # tpyc: type(list[str])
    print("words:", f_words(ws), ws[2])

    # Span and Array parameters: the callee writes an element
    sa = [1, 2]  # tpyc: type(Array[int64, 2])
    print("span:", f_span(sa), sa)
    aa = [1, 2]  # tpyc: type(Array[int64, 2])
    print("array:", f_array(aa), aa)

    # ranking: the least widening wins.
    r1 = [1, 2]  # tpyc: type(list[int32])
    print("rank default:", rank32(r1), r1)
    r2 = [1, 2]  # tpyc: type(list[int64])
    r2.append(a64())
    print("rank wide:", rank_wide(r2), r2[3])  # list[int32] would narrow
    r3 = [1]  # tpyc: type(list[int64])
    r3.append(int32(2))
    print("floor a:", floor_a(r3), r3[2])  # int64 widens less than int
    r3.append(a64())
    r5 = [1]  # tpyc: type(list[int64])
    print("typed scalar:", pair(r5, a64()), r5[1])  # the int64 picks the pair
    r6 = [1]  # tpyc: type(list[int64])
    r7 = [2]
    r7.append(a64())
    print("two locals:", two(r6, r7), r6[1], r7[2])  # r7 refuses int32
    r6.append(a64())
    r8 = [1, 2]  # tpyc: type(list[int64])
    print("generic:", gen_or_str(r8, a64()), r8[2])  # T is int64; the list follows

    # a concrete candidate that widens loses to a view that does not, whether
    # the scalar beside it matches as it is or converts
    v1 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("view vs concrete:", view_first(1, v1), v1)
    v2 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("view vs concrete, int:", view_first_big(1, v2), v2)

    # one container at two positions is applicable only where both
    # parameters want it at one type
    j1 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("joint views:", joint_views(j1, j1))
    j2 = [1, 2]  # tpyc: type(list[int64])
    print("joint wide:", joint_wide(j2, j2), j2)
    # two rows of one nested list share their leaf
    jr = [[1], [2]]  # tpyc: type(Array[list[int64], 2])
    print("joint rows:", joint_wide(jr[0], jr[1]), jr)

    # `lb = la` links the two locals' cells: the split candidate would want
    # them at two types, so it is not applicable
    la = [1, 2]
    lb = [3]
    lb = la
    print("linked:", linked(la, lb), la, len(lb))

    # `nb = na` is one list under two names: both candidates widen it once,
    # so the two-list candidate wins on specificity, not the view
    na = [1]
    nb = [7]
    nb = na
    print("two names:", two_names(na, nb), na)
    print("one name twice:", two_names(na, na), na)

    # an element of `oy` stored into `ox` ties them one way: `ox` holds what
    # `oy` holds, so the split candidate would push it past its int32
    ox = [1]  # tpyc: type(list[int64])
    oy = [2]  # tpyc: type(list[int64])
    ox.append(oy[0])
    print("one way:", one_way(oy, ox), ox, oy)

    # the least widening, declared in the order CPython's dispatch tries
    fv = [1, 2]
    print("from view big:", from_view_big(fv), fv)
    w1 = [1, 2]
    print("view first32:", view_first32(w1), w1)
    w2 = [1, 2]
    print("view first64:", view_first64(w2), w2)
    t1 = [1, 2]
    print("twin generic first:", twin_generic_first(t1, a64()), t1)
    t2 = [1, 2]
    print("twin declared first:", twin_declared_first(t2, a64()), t2)
    # a declared view in a generic candidate and in a plain one: `gv`'s list
    # candidate decides `gm1`, so a wider value fits after the call; `hv`'s
    # view decides nothing and the leaf settles when the call ends
    gm1 = [1, 2]  # tpyc: type(list[int64])
    print("generic declared view:", gv(gm1, 3), gm1)
    gm1.append(a64())
    hm1 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("plain declared view:", hv(hm1, 3), hm1, gm1[2])
    rr = [1, 2]
    print("regime c rank:", rc_rank(rr, lambda x: x + 1), rr)

    # keyword arguments bind a generic candidate's type parameter
    gk = [1, 2]  # tpyc: type(list[int64])
    print("generic keyword:", gen_kw(xs=gk, v=a64()), gk[2])

    # a nested call beside a container: as the hoisted spelling
    nc = [1, 2]  # tpyc: type(list[int64])
    print("nested call:", outer(one(1), nc), nc)
    t = one(1)
    hc = [1, 2]  # tpyc: type(list[int64])
    print("nested call hoisted:", outer(t, hc), hc)
    bc = [1, 2]  # tpyc: type(list[int64])
    print("nested builtin:", outer(len("ab"), bc), bc)

    # losing candidates leave nothing on the argument
    print("lose array:", lose_array([1, 2], 0))
    ls = [1, 2]  # tpyc: type(list[int32])
    print("lose span:", lose_span(ls, 0), ls)

    # Regime C: a lambda beside the container
    rc = [1, 2]  # tpyc: type(list[int64])
    print("regime c:", apply(rc, lambda x: x + 1), rc[2])
    rc.append(a64())
    print("regime c:", rc[3])

    # call path (b): builtin overloads -- sum's declared views decide
    # nothing; the leaf settles at the call's end.
    bs = [1, 2, 3]
    print("builtin:", sum(bs))

    # call path (d): a method group, positional and by keyword
    am = [1, 2]  # tpyc: type(list[int64])
    print("method group:", acc.add(am), am[2])
    am.append(a64())
    print("method group:", am[3])
    km = [1, 2]  # tpyc: type(list[int64])
    print("method keyword:", acc.add(xs=km), km[2])
    km.append(a64())
    print("method keyword:", km[3])

    # call path (f): a generic method and a generic constructor
    gm = [1, 2]  # tpyc: type(list[int64])
    print("generic method:", acc.push(gm, a64()), gm[2])
    hm = [1, 2]  # tpyc: type(list[int64])
    h = Holder(hm, a64())  # the generic ctor's T is int64; the list follows
    hm.append(a64())
    print("generic ctor:", h.first, hm[3])

    # call path (g): a record's operator dunder
    om = [1, 2]  # tpyc: type(list[int64])
    print("operator:", acc + om)  # the dunder overload decides om
    om.append(a64())
    print("operator:", om[2])

    acc.in_method()
    for line in in_generator():
        print("generator:", line)
    print("async:", asyncio.run(in_async()))

    # position: try / finally
    tm = [1, 2]  # tpyc: type(list[int64])
    try:
        print("try:", f_list(tm))
    finally:
        tm.append(a64())
    print("try:", tm[3])

    print("module level:", f_list32(MS), MS)


main()
