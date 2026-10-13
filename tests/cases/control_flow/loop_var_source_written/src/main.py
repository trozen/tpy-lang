# A value-type loop variable copies its element when the loop may write the
# source while it is live; a loop that cannot keeps the reference or view.
import asyncio
from typing import Any, Callable, Iterable, Iterator, cast

from tpy import Array, Own, ReturnException, Span, error_return, pure

LONG_A = "one-and-longer-than-the-small-string-buffer"
LONG_B = "changed-and-longer-than-the-small-string-buffer"

G: list[int] = [1, 2]
G2: list[int] = [1, 2]
GB: Array[int, 2] = [1, 2]


def for_big(a: list[int]) -> None:
    for y in a:
        a[0] = 50  # for, BigInt element: y keeps its value
        print("for_big:", y)


def for_str(a: list[str]) -> None:
    for s in a:  # tpyc: type(str)
        a[0] = LONG_B  # for, str element: s owns its string
        print("for_str:", s)


def for_bytes(a: list[bytes]) -> None:
    for b in a:
        a[0] = b"changed-and-longer-than-the-small-buffer"  # for, bytes element
        print("for_bytes:", b)


def for_tuple(a: list[tuple[int, str]]) -> None:
    for t in a:
        a[0] = (50, LONG_B)  # for, value tuple element
        print("for_tuple:", t[0], t[1])


def for_union(a: list[int | str]) -> None:
    for u in a:
        # for, value union element written in place; the warning is right:
        # reverse moves the elements the iterator walks
        a.reverse()  # tpyc: warning(/'reverse' invalidates the iterator/)
        print("for_union:", u)
        break


def for_unpack(a: list[tuple[int, str]]) -> None:
    for x, s in a:
        a[0] = (50, LONG_B)  # for unpack head: value targets copy
        print("for_unpack:", x, s)


def dict_items(d: dict[str, int]) -> None:
    for k, v in d.items():
        d["a"] = 50  # items() head over the dict
        print("dict_items:", k, v)


def dict_values(d: dict[str, str]) -> None:
    for v in d.values():  # tpyc: type(str)
        d["a"] = LONG_B  # values() over the dict, str element
        print("dict_values:", v)


def array_source(b: Array[int, 2]) -> None:
    for y in b:
        b[0] = 50  # for over an Array source
        print("array_source:", y)


def poke(a: list[int]) -> int:
    a[0] = a[0] + 100
    return 0


def poke_arr(a: Array[int, 3]) -> int:
    a[0] = a[0] + 100
    return 0


def comps() -> None:
    a1: list[int] = [1, 2, 3]
    print("comp_list:", [poke(a1) + y for y in a1])  # list comprehension
    a2: list[int] = [1, 2, 3]
    print("comp_set:", sorted({poke(a2) + y for y in a2}))  # set comprehension
    a3: list[int] = [1, 2]
    # nested: the outer variable is live across the inner clause
    print("comp_nested:", [[poke(a3) + y for z in range(2)] for y in a3])
    b: Array[int, 3] = [1, 2, 3]
    arr: Array[int, 3] = [poke_arr(b) + y for y in b]  # Array comprehension
    print("comp_array:", arr[0], arr[1], arr[2])


def gen_body(a: list[int]) -> Iterator[int]:
    yield 0
    for y in a:
        a[0] = 50  # generator loop that does not suspend
        print("gen_body:", y)
    yield 1


def via_callee(a: list[int]) -> None:
    for y in a:
        poke(a)  # a callee writes the source through its parameter
        print("via_callee:", y)


def via_nested_def(a: list[int]) -> None:
    def bump() -> None:
        a[0] = 50
    for y in a:
        bump()  # a nested def defined before the loop writes the source
        print("via_nested_def:", y)


class Holder:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def reset(self) -> None:
        self.xs[0] = 50

    def run(self) -> None:
        for y in self.xs:
            self.reset()  # a method writes the iterated field
            print("via_method:", y)


def reset_global() -> None:
    G[0] = 50


def walk(xs: list[int]) -> None:
    for y in xs:
        reset_global()  # a no-arg callee writes the global iterated as xs
        print("via_global:", y)


def two(a: list[int], b: list[int]) -> None:
    for y in a:
        b[0] = 50  # b may be a: two(x, x)
        print("via_alias:", y)


def alias_target(xs: list[str]) -> None:
    merged = xs
    for p in xs:  # tpyc: type(str)
        # an alias of the source is written: p copies; the warning is right:
        # the append may reallocate the list the loop walks
        merged.append(p)  # tpyc: warning(/'append' invalidates the iterator/)
        print("alias_target:", p)
        break


def nested_grid() -> None:
    grid: list[list[int]] = [[1, 2]]
    for row in grid:
        for y in row:
            grid[0][0] = 5  # row borrows from grid: the inner y copies
            print("nested_grid:", y)


def keep_owned_target(xs: list[str]) -> None:
    merged: list[str] = []
    for p in xs:  # tpyc: type(StrView)
        merged.append(p)  # inverse: a fresh local list cannot be the source
    print("keep_owned_target:", merged)


def keep_read_only(a: list[int], s: list[str]) -> None:
    total = 0
    for y in a:
        total += y  # inverse: the loop only reads
        print("keep_read_only:", y)
    n = 0
    for t in s:  # tpyc: type(StrView)
        n += len(t)
    print("keep_read_only:", total, n)


def keep_unrelated(a: list[int], d: dict[str, int], other: list[str]) -> None:
    for y in a:
        d["k"] = 5  # inverse: a dict[str, int] cannot hold the list
        other.append("x")  # inverse: a list[str] cannot hold the list
        print("keep_unrelated:", y)


def keep_enumerate(a: list[int]) -> None:
    for i, y in enumerate(a):
        a[0] = 50  # inverse: the enumerate step already copies
        print("keep_enumerate:", i, y)


class InCtor:
    total: int

    def __init__(self, a: list[int]) -> None:
        self.total = 0
        for y in a:
            a[0] = 50  # constructor body
            print("in_ctor:", y)
            self.total += y


def in_closure(a: list[int]) -> None:
    def inner() -> None:
        for y in a:
            a[0] = 50  # loop inside a closure body
            print("in_closure:", y)
    inner()


def in_try(a: list[int]) -> None:
    for y in a:
        try:
            a[0] = 50  # write inside try / finally
        finally:
            print("in_try:", y)


def in_match(a: list[int]) -> None:
    for y in a:
        match y:
            case 1:
                a[0] = 50  # write inside a match arm
            case _:
                pass
        print("in_match:", y)


async def in_async(a: list[int]) -> None:
    await asyncio.sleep(0)
    for y in a:
        a[0] = 50  # async def loop that does not suspend
        print("in_async:", y)


def comp_in_frame(a: list[int]) -> Iterator[int]:
    yield 0
    vals = [poke(a) + y for y in a]  # comprehension inside a generator frame
    yield vals[0]


class K:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __eq__(self, other: "K") -> bool:
        G2[0] = 61
        return False

    def __lt__(self, other: "K") -> bool:
        G2[0] = 63
        return self.n < other.n

    def __str__(self) -> str:
        G2[0] = 64
        return "K"


def dunder_in(ks: list[K], k: K) -> None:
    for y in G2:
        found = k in ks  # `in` runs K.__eq__, which writes the source
        print("dunder_in:", y, found)


def dunder_eq(k: K) -> None:
    for y in G2:
        same = k == k  # `==` runs K.__eq__
        print("dunder_eq:", y, same)


def dunder_fstr(k: K) -> None:
    for y in G2:
        text = f"{k}"  # the f-string runs K.__str__
        print("dunder_fstr:", y, text)


def dunder_sorted(ks: list[K]) -> None:
    for y in G2:
        ordered = sorted(ks)  # sorted runs K.__lt__
        print("dunder_sorted:", y, len(ordered))


class KA:
    def __init__(self) -> None:
        pass

    def __eq__(self, other: "KA") -> bool:
        GB[0] = 50
        return False


def eq_array_comp(k: KA, b: Array[int, 2]) -> None:
    # Array comprehension whose element runs an __eq__ writing the source
    arr: Array[int, 2] = [int(k == k) + y for y in b]
    print("eq_array_comp:", arr[0], arr[1])


class Bag:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1, 2]

    @property
    def first(self) -> int:
        return self.xs[0]

    @first.setter
    def first(self, v: int) -> None:
        self.xs[0] = v


def via_setter(b: Bag) -> None:
    for y in b.xs:
        b.first = 50  # a property setter writes the source
        print("via_setter:", y)


def del_item(a: list[int]) -> None:
    for y in a:
        # del of an element of the source; the warning is right: del shifts
        # the elements the iterator walks
        del a[0]  # tpyc: warning(/'del' invalidates the iterator/)
        print("del_item:", y)
        break


def aug_item(a: list[int]) -> None:
    for y in a:
        a[0] += 50  # augmented store into the source
        print("aug_item:", y)


def span_local(a: list[int]) -> None:
    sp: Span[int] = a
    for y in sp:
        a[0] = 50  # a Span local over the written list
        print("span_local:", y)


def slice_source(a: list[int]) -> None:
    for y in a[0:2]:
        # a slice of the written list; the warning is right: the store lands
        # in the storage the slice view walks
        a[0] = 50  # tpyc: warning(/may hit the element being iterated/)
        print("slice_source:", y)


def add_nine(r: list[int]) -> None:
    r.append(9)


def generic_touch[T](a: list[T], touch: Callable[[T], None]) -> None:
    for y in a:
        touch(y)  # generic element: the reference lends the element


def keep_isinstance(items: list[int | str]) -> int:
    n = 0
    for item in items:
        if isinstance(item, str):  # inverse: a narrowing test runs no code
            n += 1
    return n


@pure
def pure_len(a: list[int]) -> int:
    return len(a)


def keep_pure(a: list[int]) -> None:
    for y in a:
        n = pure_len(a)  # inverse: a @pure callee writes nothing
        print("keep_pure:", y, n)


def keep_generic_prod(xs: Iterable[int], start: int) -> int:
    r = start
    for x in xs:
        r = r * x  # inverse: a rebind of an int holds no storage
    return r


class W:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __eq__(self, other: "W") -> bool:
        G2[0] = 61
        return self.n == other.n

    def __hash__(self) -> int:
        G2[0] = 62
        return self.n

    def __bool__(self) -> bool:
        G2[0] = 65
        return True

    def __add__(self, other: "W") -> Own["W"]:
        G2[0] = 66
        return W(self.n + other.n)

    def __neg__(self) -> Own["W"]:
        G2[0] = 67
        return W(-self.n)

    def __str__(self) -> str:
        G2[0] = 68
        return "W"


def hash_display(w: W) -> None:
    for y in G2:
        # a set display hashes the record: __hash__ writes G2; the warning is
        # right: the set holds its own copy of w
        s = {w}  # tpyc: warning(/copies W into owned storage/)
        print("hash_display:", y, len(s))


def dict_key(d: dict[W, int], w: W) -> None:
    for y in G2:
        v = d.get(w, 0)  # a dict lookup hashes the key
        print("dict_key:", y, v)


def list_count(ws: list[W], w: W) -> None:
    for y in G2:
        n = ws.count(w)  # list.count runs W.__eq__
        print("list_count:", y, n)


def truth_test(w: W) -> None:
    for y in G2:
        if w:  # a truth test runs W.__bool__
            print("truth_test:", y)


def binary_dunder(w: W) -> None:
    for y in G2:
        z = w + w  # `+` runs W.__add__
        print("binary_dunder:", y, z.n)


def unary_dunder(w: W) -> None:
    for y in G2:
        z = -w  # unary `-` runs W.__neg__
        print("unary_dunder:", y, z.n)


def print_record(w: W) -> None:
    for y in G2:
        print("print_record: w", w)  # W.__str__ writes the element y reads
        print("print_record:", y)


def match_guard(w: W, other: W) -> None:
    for y in G2:
        match w:
            case x if x == other:  # the guard runs W.__eq__
                print("match_guard: equal")
            case _:
                pass
        print("match_guard:", y)


class Counter:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> "Counter":
        return self

    def __next__(self) -> int:
        G2[0] = 69
        if self.n >= 1:
            raise StopIteration
        self.n += 1
        return self.n


def user_iterable() -> None:
    for y in G2:
        for z in Counter():  # the inner loop runs Counter.__next__
            print("user_iterable:", y, z)


class Mgr:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "Mgr":
        G2[0] = 70
        return self

    def __exit__(self, kind, value, tb) -> None:
        G2[0] = 71


def with_manager() -> None:
    for y in G2:
        with Mgr():  # the manager's enter and exit write G2
            pass
        print("with_manager:", y)


def call_param(f: Callable[[], None]) -> None:
    for y in G2:
        f()  # a call through a Callable parameter
        print("call_param:", y)


def set_g2() -> None:
    G2[0] = 72


class Drop:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __del__(self) -> None:
        G2[0] = G2[0] + 100


def del_record() -> None:
    d = Drop(1)
    for y in G2:
        if y == 1:
            del d  # deleting the record runs Drop.__del__
        print("del_record:", y)
        break


class Slot:
    d: Drop

    def __init__(self, d: Own[Drop]) -> None:
        self.d = d


# The warning is right: a store inside a loop copies `e`, it cannot move it.
def field_replace(h: Slot, e: Own[Drop]) -> None:  # tpyc: warning(/'e' is never consumed/)
    for y in G2:
        h.d = e  # the store destroys the old Drop, whose __del__ writes G2
        print("field_replace:", y)
        break


def span_param(sp: Span[int], a: list[int]) -> None:
    for y in sp:
        a[0] = 50  # a Span parameter over the written list
        print("span_param:", y)


class Viewed:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def view(self) -> Span[int]:
        return self.xs


def span_call(v: Viewed) -> None:
    sp = v.view()
    for y in sp:
        v.xs[0] = 50  # a Span call result over the written list
        print("span_call:", y)


def span_write(a: list[int]) -> None:
    sp: Span[int] = a
    for y in a:
        sp[0] = 50  # a write through a Span local into the iterated list
        print("span_write:", y)


class Plain:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "Plain":
        return self

    def __exit__(self, kind, value, tb) -> None:
        pass


def loop_in_with(a: list[int]) -> None:
    with Plain():
        for y in a:
            a[0] = 50  # a loop inside a `with` body
            print("loop_in_with:", y)


class Missing(Exception, ReturnException):
    pass


@error_return(Missing)
def loop_in_error_return(a: list[int]) -> int:
    for y in a:
        a[0] = 50  # a loop inside an @error_return body
        print("loop_in_error_return:", y)
    if len(a) == 0:
        raise Missing
    return 0


def comp_condition(a: list[int]) -> None:
    # a comprehension whose condition writes the source
    out = [y for y in a if poke(a) == 0]
    print("comp_condition:", out)


def dict_comp(a: list[int]) -> None:
    # dict comprehension whose value writes the source
    out = {y: poke(a) + y for y in a}
    print("dict_comp:", out)


def keep_cast(a: list[int]) -> None:
    for y in a:
        z = cast(int, y)  # inverse: a cast runs nothing
        print("keep_cast:", y, z)


G3: list[int] = [1, 2]


class Peek:
    def __init__(self) -> None:
        pass

    @property
    def first(self) -> int:
        G3[0] = 50
        return G3[1]


def via_getter(h: Peek) -> None:
    for y in G3:
        v = h.first  # a property getter writes the source
        print("via_getter:", y, v)


def keep_inert_ops(a: list[int], d: dict[int, int], s: list[str]) -> None:
    n = 0
    for y in a:
        # inverse: truth tests, compares, membership, count, a set display,
        # a dict lookup, an f-string and a nested loop over inert values
        if y:
            n += 1
        while n > 100:
            n -= 1
        assert y == y
        if y in a and y == 1:
            n += a.count(y)
        seen = {y}
        n += d.get(y, 0) + len(seen)
        text = f"{y}"
        for z in range(2):
            n += z
        print("keep_inert_ops:", y, text, n)
    for t in s:  # tpyc: type(StrView)
        text = f"{t}"  # inverse: an f-string of a str view
        print("keep_inert_ops:", text)


def post_loop_str() -> None:
    a = ["b", LONG_A]
    for s in a:  # tpyc: type(str)
        pass
    a[-1] = LONG_B  # the variable is read after the loop: it copies
    print("post_loop_str:", s)


def post_loop_bytes() -> None:
    a = [b"x", b"one-and-longer-than-the-small-bytes-buffer"]
    for b in a:  # tpyc: type(bytes)
        pass
    a[-1] = b"changed"  # the variable is read after the loop: it copies
    print("post_loop_bytes:", b)


def post_loop_rebind() -> None:
    a = ["b", LONG_A]
    for s in a:  # tpyc: type(str)
        pass
    a = [LONG_B]  # the rebind frees the list the variable was read from
    print("post_loop_rebind:", s, len(a))


def post_loop_unpack() -> None:
    a = [(1, LONG_A)]
    for n, s in a:
        pass
    a[-1] = (50, LONG_B)  # the targets are read after the loop: they copy
    print("post_loop_unpack:", n, s)


class Shown:
    def __init__(self) -> None:
        pass

    def __repr__(self) -> str:
        G4[0] = "REPR-WROTE-and-longer-than-the-small-string-buffer"
        return "Shown"


G4: list[str] = [LONG_A, "two"]


def any_record(a: list[str], h: Any) -> None:
    for y in a:
        print(h)  # an Any holding a record: printing it runs its __repr__
        print("any_record:", y)
        break


def keep_comp_in_body(lines: list[str]) -> None:
    n = 0
    for line in lines:  # tpyc: type(StrView)
        # inverse: a comprehension over inert values writes nothing
        parts = [p for p in line.split(",") if p]
        n += len(parts)
    print("keep_comp_in_body:", n)


def poke_str(a: list[str]) -> int:
    a[0] = "changed-and-longer-than-the-small-string-buffer"
    return 0


def comp_in_body_writes(a: list[str]) -> None:
    for s in a:  # tpyc: type(str)
        z = [poke_str(a) for _ in range(1)]  # the comprehension writes the source
        print("comp_in_body_writes:", s, len(z))
        break


# A comprehension element reads an enclosing name once per item: never its
# last use, so the copied variable is never moved out (every item sees it).
def comp_reads_copy(a: list[str], n: list[int]) -> None:
    for s in a:
        a[0] = LONG_B
        z = [s for _ in n]  # the element reads the copy per item
        d = {i: s for i in range(2)}
        e = {s for _ in range(2)}
        g = list(s for _ in range(2))
        print("comp_reads_copy:", z, d, e, g)


# A comprehension over an undecided str local holds its element owned.
def comp_str_local(a: list[str]) -> None:
    t = a[0]
    a[0] = LONG_B
    z = [t for _ in range(2)]  # the element type settles to str
    print("comp_str_local:", z)


def comp_walrus_str(ys: list[str]) -> None:
    last = ""
    out = [(last := s) for s in ys]  # the walrus target settles to str
    print("comp_walrus_str:", last, len(out))


class Taken:
    v: list[int]

    def __init__(self, n: int) -> None:
        self.v = [i for i in range(n)]


class Sink:
    items: list[Taken]

    def __init__(self) -> None:
        self.items = []

    def take(self, t: Own[Taken]) -> int:
        self.items.append(t)
        return len(self.items[-1].v)


# An owned local read in a comprehension element is not moved per item
# (TPy copies where CPython aliases; only lengths are printed).
def comp_own_arg(k: Sink) -> None:
    t = Taken(2)
    z = [k.take(t) for _ in range(2)]  # tpyc: warning(/copies Taken into owned storage/)
    print("comp_own_arg:", z)


# A copying for-head target moves at its last use in the pass.
def head_move(xs: list[tuple[str, str]]) -> None:
    out: list[str] = []
    for a, b in xs:
        xs[0] = (LONG_B, b)
        out.append(a)  # the last use of `a` in the pass moves it
    print("head_move:", out, xs[0][0] == LONG_B)


G5: list[str] = [LONG_A, "two"]


class Rows:
    rows: list[list[int]]

    def __init__(self) -> None:
        self.rows = [[1], [2]]

    def __getitem__(self, i: int) -> list[int]:
        G5[0] = LONG_B
        return self.rows[i]


# A class `__getitem__` returning a reference is the call it is, not an
# element read: it writes the source, so the loop variable copies.
def getitem_ref_writes(r: Rows) -> None:
    for s in G5:  # tpyc: type(str)
        n = len(r[0])  # runs Rows.__getitem__
        print("getitem_ref_writes:", s, n)


def main() -> None:
    for_big([1, 2])
    for_str([LONG_A, "two"])
    for_bytes([b"one-and-longer-than-the-small-buffer", b"two"])
    for_tuple([(1, LONG_A), (2, "b")])
    for_union([1, "two-and-longer-than-the-small-string-buffer"])
    for_unpack([(1, LONG_A), (2, "b")])
    dict_items({"a": 1, "b": 2})
    dict_values({"a": LONG_A, "b": "two"})
    arr: Array[int, 2] = [1, 2]
    array_source(arr)
    comps()
    pulled = [v for v in gen_body([1, 2])]
    print("gen_body:", pulled)
    via_callee([1, 2])
    via_nested_def([1, 2])
    Holder().run()
    walk(G)
    x = [1, 2]
    two(x, x)
    alias_target([LONG_A, "b"])
    nested_grid()
    keep_owned_target([LONG_A, "b"])
    keep_read_only([1, 2], [LONG_A, "b"])
    keep_unrelated([1, 2], {"k": 0}, ["o"])
    keep_enumerate([1, 2])
    InCtor([1, 2])
    in_closure([1, 2])
    in_try([1, 2])
    in_match([1, 2])
    asyncio.run(in_async([1, 2]))
    framed = [v for v in comp_in_frame([1, 2])]
    print("comp_in_frame:", framed)
    G2[0] = 1
    dunder_in([K(1), K(2)], K(3))
    G2[0] = 1
    dunder_eq(K(1))
    G2[0] = 1
    dunder_fstr(K(1))
    G2[0] = 1
    dunder_sorted([K(2), K(1)])
    eq_array_comp(KA(), GB)
    via_setter(Bag())
    del_item([1, 2])
    aug_item([1, 2])
    span_local([1, 2])
    slice_source([1, 2])
    rows: list[list[int]] = [[1], [2]]
    generic_touch(rows, add_nine)
    print("generic_touch:", rows)
    print("keep_isinstance:", keep_isinstance([1, "q"]))
    keep_pure([1, 2])
    print("keep_generic_prod:", keep_generic_prod([2, 3], 1))
    G2[0] = 1
    hash_display(W(1))
    keyed = {W(1): 5}
    G2[0] = 1
    dict_key(keyed, W(1))
    G2[0] = 1
    list_count([W(1), W(2)], W(1))
    G2[0] = 1
    truth_test(W(1))
    G2[0] = 1
    binary_dunder(W(1))
    G2[0] = 1
    unary_dunder(W(1))
    G2[0] = 1
    print_record(W(1))
    G2[0] = 1
    match_guard(W(1), W(2))
    G2[0] = 1
    user_iterable()
    G2[0] = 1
    with_manager()
    G2[0] = 1
    call_param(set_g2)
    G2[0] = 1
    del_record()
    G2[0] = 1
    field_replace(Slot(Drop(3)), Drop(4))
    G2[0] = 1
    sl = [1, 2]
    span_param(sl, sl)
    span_call(Viewed())
    span_write([1, 2])
    loop_in_with([1, 2])
    try:
        loop_in_error_return([1, 2])
    except Missing:
        print("loop_in_error_return: missing")
    comp_condition([1, 2])
    dict_comp([1, 2])
    keep_cast([1, 2])
    via_getter(Peek())
    keep_inert_ops([1, 2], {1: 5}, [LONG_A, "b"])
    post_loop_str()
    post_loop_bytes()
    post_loop_rebind()
    post_loop_unpack()
    any_record(G4, Shown())
    keep_comp_in_body(["a,b", "c"])
    comp_in_body_writes([LONG_A, "b"])
    comp_reads_copy([LONG_A, "b"], [1, 2])
    comp_str_local([LONG_A, "b"])
    comp_walrus_str([LONG_A, LONG_B])
    comp_own_arg(Sink())
    head_move([(LONG_A, "x"), ("y", "z")])
    getitem_ref_writes(Rows())


MOD: list[int] = [1, 2]
for mod_y in MOD:
    MOD[0] = 50  # module level loop
    print("module_level:", mod_y)

main()
