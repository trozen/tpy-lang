# A getter read off a TEMPORARY (`mk().data`) at an argument the callee keeps
# nothing of is the bound twin's borrow: copied with its warning, or aliased.
import asyncio
from typing import Any, Callable, Iterator, Optional
from tpy import (Own, Ptr, ReturnException, StrView, ValueType, error_return,
                 int32, readonly)


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class VS(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Pt:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, o: "Pt") -> bool:
        return self.n == o.n

    def __lt__(self, o: "Pt") -> bool:
        return self.n < o.n

    def __add__(self, o: "Pt") -> "Own[Pt]":
        return Pt(self.n + o.n)

    def __radd__(self, o: int32) -> "Own[Pt]":
        return Pt(o + self.n)

    def __neg__(self) -> "Own[Pt]":
        return Pt(-self.n)

    def __str__(self) -> str:
        return f"Pt({self.n})"


class Bag:
    items: list[str]

    def __init__(self) -> None:
        self.items = ["p", "q"]

    def __eq__(self, o: list[str]) -> bool:
        return self.items == o


class Ret:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __add__(self, o: "Ret") -> "Ret":
        return o


class Tag:
    name: str

    def __init__(self) -> None:
        self.name = "hello"

    def __eq__(self, o: StrView) -> bool:
        return self.name == o

    def __add__(self, o: StrView) -> int32:
        return len(o)


class Grow:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __add__(self, o: StrView) -> "Own[Grow]":
        g = Grow()
        g.n = self.n + len(o)
        return g


class Rec:
    x: int32
    tags: list[str]
    vs: VS
    p: Pt
    buf: bytearray
    s: str
    views: list[StrView]
    table: dict[str, int32]
    keys: set[int32]
    pair: tuple[Pt, int32]
    opt: Optional[Pt]
    ret: Ret

    def __init__(self, x: int32) -> None:
        self.x = x
        self.tags = ["p", "q"]
        self.vs = VS(7)
        self.p = Pt(3)
        self.buf = bytearray(b"ab")
        self.s = "hello world, a string long enough for the heap"
        self.views = []
        self.views.append("v")
        self.table = {"a": 1}
        self.keys = {4, 5}
        self.pair = (Pt(1), 2)
        self.opt = Pt(4)
        self.ret = Ret(6)


G: list[Box] = [Box(10), Box(20)]
GREC: Rec = Rec(1)


class H:
    items: list[Box]
    names: list[str]
    keys: set[int32]
    table: dict[int32, int32]
    nums: list[int32]
    r: Rec

    def __init__(self) -> None:
        self.items = [Box(1), Box(2)]
        self.names = ["a", "b"]
        self.keys = {1, 2}
        self.table = {1: 5}
        self.nums = [3, 4]
        self.r = Rec(6)

    @property
    def data(self) -> list[Box]:
        return self.items

    @property
    def gdata(self) -> list[Box]:
        return G

    @property
    def nm(self) -> list[str]:
        return self.names

    @property
    def ks(self) -> set[int32]:
        return self.keys

    @property
    def tb(self) -> dict[int32, int32]:
        return self.table

    @property
    def nu(self) -> list[int32]:
        return self.nums

    @property
    def rec(self) -> Rec:
        return self.r

    @property
    def grec(self) -> Rec:
        return GREC


def mk() -> Own[H]:
    return H()


def free_section() -> None:
    # free function: list `+=` / extend / `list()` / append copy the elements
    acc: list[Box] = []
    acc += mk().data  # tpyc: warning(/copies Box elements/)
    acc.extend(mk().gdata)  # tpyc: warning(/copies Box elements/)
    l2 = list(mk().data)  # tpyc: warning(/copies Box elements/)
    acc[0].n = 99
    l2[1].n = 77
    print("free:", len(acc), acc[0].n, acc[1].n, acc[2].n, l2[0].n, l2[1].n,
          G[0].n)
    # set update / `|=`, dict update, str join: elements hold no pointer
    ss: set[int32] = set()
    ss.update(mk().ks)  # tpyc: ok
    ss |= mk().ks  # tpyc: ok
    dd: dict[int32, int32] = {}
    dd.update(mk().tb)  # tpyc: ok
    print("free_set_dict:", len(ss), dd[1], "-".join(mk().nm))  # tpyc: ok
    # scalars read off a temporary are copied out
    a = mk().rec.x  # tpyc: ok
    b = mk().nu[0]  # tpyc: ok
    print("free_scalar:", a, b)


def ret_any() -> Any:
    # return: a scalar read off a temporary is copied out, then boxed as Any
    return mk().rec.x  # tpyc: ok


def gen_any() -> Iterator[Any]:
    # generator: the same at a yield
    yield mk().rec.x  # tpyc: ok


def read_vs(p: Ptr[readonly[VS]]) -> int32:
    return p.x


def any_print_section() -> None:
    # a conversion to Any reads the scalar's value, never its address
    a: Any = mk().rec.x  # tpyc: ok
    b: Any = 1
    b = mk().rec.x  # tpyc: ok
    xs: list[Any] = []
    xs.append(mk().rec.x)  # tpyc: ok
    print("any:", a, b, len(xs), ret_any(), list(gen_any()))
    # a print and str() read a list off a temporary inside the statement
    print(*mk().rec.tags)  # tpyc: ok
    # no repr() leg: BUGS.md#repr-property-chain-container-field-rejects
    print("print_str:", str(mk().rec.tags))  # tpyc: ok
    # a pointer to a temporary's ValueType field at a parameter that keeps
    # nothing; a ValueType read is a value copy, with no alias to observe
    n = read_vs(mk().rec.vs)  # tpyc: ok
    v = mk().rec.vs  # tpyc: ok
    print("ptr:", n, v.x)


def operator_section() -> None:
    # operators: each operand is an argument of the dunder it resolves to, so
    # one its dunder keeps nothing of reads the temporary's field in place --
    # the parameter, the receiver, a reflected dunder's receiver, a unary one
    print("op_cmp:", Pt(3) == mk().rec.p, mk().rec.p == Pt(4),  # tpyc: ok
          Pt(1) < mk().rec.p, Bag() == mk().rec.tags)  # tpyc: ok
    # a fresh result; mutating it leaves the temporary's field untouched
    s = Pt(1) + mk().rec.p  # tpyc: ok
    s.n += 10
    t = 5 + mk().rec.p  # tpyc: ok
    u = -mk().rec.p  # tpyc: ok
    print("op_arith:", s.n, t.n, u.n, (mk().rec.p + Pt(2)).n)  # tpyc: ok
    # str() reads a record's and a bytearray's field inside the statement
    print("op_str:", str(mk().rec.p), str(mk().rec.buf))  # tpyc: ok
    # a bodied dunder taking a view keeps nothing of it, at the operator
    tg = Tag()
    print("op_view:", tg == mk().rec.s, tg + mk().rec.s)  # tpyc: ok
    # the receiver of a dunder that returns only its parameter is not kept
    print("op_recv:", (mk().rec.ret + Ret(1)).k)  # tpyc: ok
    # `+=` falling back to `__add__` reads the operand as the binop does
    g = Grow()
    g += mk().rec.s  # tpyc: warning(/falls back to '__add__'/)
    print("op_aug:", g.n)


def builtin_compare_section() -> None:
    # builtin container compares read both operands inside the statement:
    # list, dict, set, tuple and Optional, as a decl init, under `not`, in
    # a comprehension, an f-string and an assert
    h = H()
    print("cmp_list:", h.rec.tags == mk().rec.tags,  # tpyc: ok
          mk().rec.views != h.rec.views)  # tpyc: ok
    print("cmp_dict_set:", h.rec.table == mk().rec.table,  # tpyc: ok
          mk().rec.keys == h.rec.keys)  # tpyc: ok
    print("cmp_tuple_opt:", h.rec.pair == mk().rec.pair,  # tpyc: ok
          h.rec.opt == mk().rec.opt)  # tpyc: ok
    same = h.rec.views == mk().rec.views  # tpyc: ok
    flags = [h.rec.tags != mk().rec.tags for _ in range(1)]  # tpyc: ok
    assert h.rec.keys == mk().rec.keys  # tpyc: ok
    print("cmp_pos:", same, not (h.rec.table == mk().rec.table), flags,  # tpyc: ok
          f"{mk().rec.tags == h.rec.tags}")  # tpyc: ok


def chained_compare_section(flag: bool) -> None:
    # a chained comparison binds its middle operand ahead of the pairs, so a
    # scalar read off a temporary is copied into that binding
    print("chain_scalar:", 1 < mk().rec.x < 9, 0 < mk().nu[0] < 9 < 10)  # tpyc: ok
    # durable middles stay bound in place: a local, a named receiver's
    # getter, a global, a fresh value the binding itself holds
    h = H()
    p = Pt(3)
    print("chain_durable:", Pt(1) < p < Pt(9),  # tpyc: ok
          Pt(1) < h.rec.p < Pt(9), "a" < h.rec.s < "z",  # tpyc: ok
          Pt(1) < GREC.p < Pt(9), Pt(1) < p + Pt(1) < Pt(9))  # tpyc: ok
    # a wholly fresh operand is a value the binding extends: a tuple literal
    # endpoint, an f-string, a select with a fresh arm (the record one holds
    # that arm in a slot of its own)
    print("chain_fresh:", (1, 2) == (1, 2) == (1, 2),  # tpyc: ok
          "0" < f"{mk().r.x}" < "9",  # tpyc: ok
          "a" < ("m" if flag else str(mk().r.x)) < "z",  # tpyc: ok
          Pt(1) < (p if flag else Pt(2)) < Pt(9))  # tpyc: ok
    # a non-scalar member of a fresh owner: the binding extends the owner
    print("chain_member:", Pt(1) < mk().r.p < Pt(9))  # tpyc: ok


class Acc:
    items: list[Box]

    def __init__(self) -> None:
        # constructor: extend copies into the field being built
        self.items = []
        self.items.extend(mk().data)  # tpyc: warning(/copies Box elements/)

    def fill(self) -> None:
        # method: `+=` on a field
        self.items += mk().data  # tpyc: warning(/copies Box elements/)


def gen() -> Iterator[int32]:
    # generator: a frame local takes the copy
    acc: list[Box] = []
    acc += mk().data  # tpyc: warning(/copies Box elements/)
    acc[0].n = 41
    yield acc[0].n
    yield len(acc)


async def coro() -> int32:
    # async: set update across a suspension-free body
    ss: set[int32] = set()
    ss.update(mk().ks)  # tpyc: ok
    await asyncio.sleep(0)
    return len(ss)


def bump(r: Rec) -> None:
    r.x += 1


class Bumper:
    def __init__(self, r: Rec) -> None:
        r.x += 10

    def bump(self, r: Rec) -> None:
        r.x += 100


def bodied_section() -> None:
    # bodied free function, constructor and method that write through the
    # argument: they keep nothing of it, so it aliases the getter's global
    bump(mk().grec)  # tpyc: ok
    b = Bumper(mk().grec)  # tpyc: ok
    b.bump(mk().grec)  # tpyc: ok
    print("bodied:", GREC.x)


class Empty(Exception, ReturnException):
    pass


@error_return(Empty)
def er_section() -> int32:
    # @error_return body
    acc: list[Box] = []
    acc.extend(mk().data)  # tpyc: warning(/copies Box elements/)
    if len(acc) == 0:
        raise Empty
    return len(acc)


class Guard:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, kind, value, tb) -> None:
        pass


def main() -> None:
    free_section()
    bodied_section()
    any_print_section()
    operator_section()
    builtin_compare_section()
    chained_compare_section(True)
    acc = Acc()
    acc.fill()
    acc.items[0].n = 5
    print("ctor_method:", len(acc.items), acc.items[0].n, acc.items[2].n)
    print("gen:", [v for v in gen()])
    print("async:", asyncio.run(coro()))
    try:
        n = er_section()
    except Empty:
        n = -1
    print("error_return:", n)
    # comprehension: str join in the element
    joined = ["-".join(mk().nm) for _ in range(2)]  # tpyc: ok
    print("comp:", joined)
    # lambda: str join in the body
    joiner: Callable[[], str] = lambda: "+".join(mk().nm)  # tpyc: ok
    print("lambda:", joiner())
    # with body
    acc2: list[Box] = []
    with Guard():
        acc2 += mk().data  # tpyc: warning(/copies Box elements/)
    acc2[1].n = 8
    print("with:", acc2[0].n, acc2[1].n)
    # try / finally
    ss: set[int32] = set()
    try:
        ss |= mk().ks  # tpyc: ok
    finally:
        print("try_finally:", len(ss))
    # match arm
    acc3: list[Box] = []
    match len(ss):
        case 2:
            acc3.extend(mk().data)  # tpyc: warning(/copies Box elements/)
        case _:
            pass
    print("match:", len(acc3), G[0].n)


# module-level statement: the global slot takes the copy
MOD_ACC: list[Box] = []
MOD_ACC += mk().data  # tpyc: warning(/copies Box elements/)
MOD_ACC[0].n = 3
print("module:", len(MOD_ACC), MOD_ACC[0].n, MOD_ACC[1].n)

main()
