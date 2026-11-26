# The variable takes its own element's form whatever the hidden name's:
# storage tuple, Optional[tuple] param, narrowed union, isinstance name,
# str / readonly params, rebound record, Optional element, unpack heads,
# set / dict / Array forms.
from typing import Callable, Iterable, Iterator, Optional
from tpy import Own, int32, readonly


class Box:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Dog:
    def __init__(self, n: int) -> None:
        self.n = n


def storage_tuple(pairs: list[tuple[int32, int32]]) -> None:
    t = (Box(1), Box(2))
    r = [t[0] + t[1] for t in pairs]  # tpyc: ok
    t[0].x += 10
    print("storage_tuple", r, t[0].x, t[1].x)


def opt_tuple(t: Optional[tuple[int32, int32]],
              xs: list[tuple[int32, int32]]) -> None:
    print("opt_tuple", [t[0] for t in xs], t is None)  # tpyc: ok


def narrowed(v: int | str) -> int:
    if isinstance(v, int):
        ws = [v for v in range(3)]  # tpyc: ok
        return len(ws) + v
    return -1


def narrowed32(v: int32 | str) -> int32:
    if isinstance(v, int32):
        # The variable is range's element; the outer v stays narrowed.
        ws = [v for v in range(3)]  # tpyc: ok
        return len(ws) + v
    return -1


def dog(p: Dog | int, xs: list[int]) -> None:
    if isinstance(p, Dog):
        print("dog", [p + 1 for p in xs], p.n)  # tpyc: ok
        p.n += 1
        print("dog_after", p.n)


def dog_lambda(p: Dog | int) -> None:
    if isinstance(p, Dog):
        # A lambda parameter hides the isinstance-narrowed name.
        f: Callable[[int], int] = lambda p: p + 1  # tpyc: ok
        print("dog_lambda", f(1), p.n)


def str_pairs() -> Iterator[tuple[str, int32]]:
    yield ("x" + str(1), 1)
    yield ("y" + str(2), 2)


def bytes_pairs() -> Iterator[tuple[bytes, int32]]:
    yield (b"x" + bytes([49]), 1)
    yield (b"y" + bytes([50]), 2)


def step_view_tuple(ps: list[tuple[str, int32]],
                    bps: list[tuple[bytes, int32]]) -> None:
    # A step view inside a tuple element is held owned by the result.
    t = [(s, n) for s, n in str_pairs()]  # tpyc: ok
    d = {n: (s, n) for s, n in str_pairs()}  # tpyc: ok
    # The bare element, as before.
    z = [s for s, n in str_pairs()]  # tpyc: ok
    # Over a borrowed list the head views the element; the tuple owns it.
    tl = [(s, n) for s, n in ps]  # tpyc: ok
    print("step_view_tuple", t, d, z, tl)
    # The bytes twin: a BytesView step view, held as Bytes.
    tb = [(b, n) for b, n in bytes_pairs()]  # tpyc: ok
    zb = [b for b, n in bytes_pairs()]  # tpyc: ok
    tbl = [(b, n) for b, n in bps]  # tpyc: ok
    print("step_view_bytes", tb, zb, tbl)


def view_hoist(flag: bool, pairs: list[tuple[str, int32]]) -> None:
    # The head's view of the step is no binding of the outer s: s keeps
    # its own (view) storage.
    if flag:
        s = "hello"
        r = [len(s) for s, n in pairs]  # tpyc: ok
    else:
        s = "world"
        r = [0]
    print("view_hoist", s, r)


def rec_self_shadow(v: list[Box]) -> None:
    # The attribute read is the element's; the source is the outer v.
    r = [v.x for v in v]  # tpyc: ok
    v[0].x += 1
    print("rec_self_shadow", r, v[0].x)


def opt_tuple_elem(p: Optional[Box], xs: list[int32],
                   ps: list[tuple[Optional[Box], int32]]) -> None:
    # A pointer-Optional name lands in its tuple element whole, None
    # included: the hidden head and a parameter alike. The copy is the
    # warned one (copy semantics intended), so nothing mutates after.
    a = [(n, b) for b, n in ps]  # tpyc: warning(/copies Box \| None into owned storage/)
    c = [(p, x) for x in xs]  # tpyc: warning(/copies Box \| None into owned storage/)
    u = [(p, 1)]  # tpyc: warning(/copies Box \| None into owned storage/)
    # The for twin.
    out: list[tuple[Optional[Box], int32]] = []
    for b, n in ps:
        out.append((b, n))  # tpyc: warning(/copies Box \| None into owned storage/)
    print("opt_tuple_elem", [n for n, b in a], [b is None for n, b in a],
          [q is None for q, x in c], [q is None for q, k in u],
          [b.x for b, n in out if b is not None])


def own_box_pairs() -> Iterator[Own[tuple[Optional[Box], int32]]]:
    yield (Box(1), 1)
    yield (None, 2)


def own_opt_tuple_elem() -> None:
    # An owning step's Optional head moves into its tuple element.
    ws = [(b, n) for b, n in own_box_pairs()]  # tpyc: ok
    print("own_opt_tuple_elem", [b is None for b, n in ws])


class Cell[T]:
    v: T

    def __init__(self) -> None:
        pass

    def put(self, x: T) -> T:
        # Right: at a reference-type T this copies where Python aliases.
        self.v = x  # tpyc: warning(/may copy T into field/)
        return x


def inferred_generic(cs: list[Cell[int32]], big: int) -> None:
    c = Cell()
    c.put(1)
    # The variable is cs's Cell[int32], not the outer c's inferred one.
    r = [c.put(big) for c in cs]  # tpyc: ok
    cs[0].put(7)
    print("inferred_generic", r, c.v, cs[0].v)


def str_param(s: str, xs: list[str]) -> None:
    print("str_param", [s + "!" for s in xs], s)  # tpyc: ok


def ro_param(b: readonly[Box], bs: list[Box]) -> None:
    r = [b.x for b in bs]  # tpyc: ok
    bs[0].x += 1
    print("ro_param", r, b.x, bs[0].x)


def rebound(k: bool, ps: list[Box]) -> None:
    a = Box(1)
    if k:
        a = Box(2)
    s = [a.x for a in ps]  # tpyc: ok
    a.x += 50
    print("rebound", s, a.x)


def opt_elem(opts: list[Optional[int]]) -> None:
    # No hide: a not-None filter narrows the element as a for filter would.
    print("opt_elem", [v + 1 for v in opts if v is not None])  # tpyc: ok


def heads(pairs: list[tuple[str, int32]], d: dict[str, Box]) -> None:
    k = "outer"
    print("str_head", [k + "!" for k, n in pairs], k)  # tpyc: ok
    print("items_head", [len(k) + c.x for k, c in d.items()])  # tpyc: ok
    for k1, c1 in d.items():
        print("for_head", len(k1) + c1.x)
    print("dup_head", [x for x, x in pairs])  # tpyc: ok
    # `x, x` in one clause is one variable, whatever the clauses after.
    print("dup_two_clauses", [x * 2 + j for x, x in [(1, 2)] for j in range(2)])  # tpyc: ok
    d["k"].x = 9
    print("after_heads", d["k"].x, k)


def forms(v: int32, xs: list[int32]) -> None:
    print("set", sorted({v * 2 for v in xs}), v)  # tpyc: ok
    print("dict", {v: v + 1 for v in xs}, v)  # tpyc: ok
    arr = [v * 3 for v in range(3)]  # tpyc: ok
    print("array_range", arr, v)
    src = [4, 5, 6]
    print("array_source", [v + 1 for v in src], v)  # tpyc: ok
    ps = [(1, 2), (3, 4)]
    print("array_unpack", [v + w for v, w in ps], v)  # tpyc: ok


def gen_pairs(ps: list[Box]) -> Iterator[tuple[Optional[Box], int32]]:
    for p in ps:
        yield (p, p.x)


def tuple_step(t: tuple[Optional[Box], int32]) -> int32:
    return t[1] if t[0] is not None else -1


def gen_storage_tuple(ps: list[Box]) -> None:
    # No hide: a tuple step off a generator binds as the for statement's.
    print("gen_tuple", [tuple_step(t) for t in gen_pairs(ps)])  # tpyc: ok


def enum_boxes[T](xs: Iterable[T]) -> Iterator[tuple[int32, T]]:
    i = 0
    for x in xs:
        yield (i, x)
        i += 1


def pointer_tuple_head(bs: list[Box]) -> None:
    # A head over a pointer-tuple step reads through it, as a for head does.
    print("ptr_tuple_head", [b.x + i for i, b in enum_boxes(bs)])  # tpyc: ok
    bs[0].x += 1
    print("ptr_tuple_after", bs[0].x)


def main() -> None:
    pointer_tuple_head([Box(5), Box(6)])
    gen_storage_tuple([Box(1), Box(2)])
    storage_tuple([(1, 2), (3, 4)])
    opt_tuple((9, 9), [(1, 2), (3, 4)])
    opt_tuple(None, [(5, 6)])
    print("narrowed", narrowed(7), narrowed("a"))
    # int32(7): an int literal is no int32 instance under CPython.
    print("narrowed32", narrowed32(int32(7)), narrowed32("a"))
    dog(Dog(1), [1, 2])
    dog_lambda(Dog(2))
    step_view_tuple([("ab", 1), ("c", 2)], [(b"ab", 1), (b"c", 2)])
    view_hoist(True, [("abc", 1), ("de", 2)])
    rec_self_shadow([Box(1), Box(2)])
    opt_tuple_elem(Box(5), [1], [(Box(1), 1), (None, 2)])
    opt_tuple_elem(None, [1], [(None, 3)])
    own_opt_tuple_elem()
    inferred_generic([Cell()], 5)
    str_param("s", ["a", "b"])
    ro_param(Box(3), [Box(4)])
    rebound(True, [Box(7), Box(8)])
    opt_elem([1, None, 3])
    heads([("a", 1), ("b", 2)], {"k": Box(3)})
    forms(1, [1, 2])
