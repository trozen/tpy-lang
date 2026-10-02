# Element reads off an Optional container receiver sema could NOT prove
# non-None: the receiver is wrapped in the runtime null check the warning
# promises (`::tpy::deref_check(d)` on a pointer binding,
# `::tpy::deref_optional_check(h.d)` on a storage optional), at every position.
# Every receiver here is non-None; the None faces are the panic_ cases.
import asyncio
from typing import Iterator, Optional

from tpy import Fn, Own, dispatch, int32, readonly


class P:
    x: int32
    rows: list[int32]
    pair: tuple[int32, int32]

    def __init__(self, x: int32) -> None:
        self.x = x
        self.rows = [x, x + 1]
        self.pair = (x, x * 2)


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(7)]

    def __getitem__(self, i: int32) -> P:
        return self.items[i]


class H:
    d: Optional[list[P]]
    lst: list[int32] | None

    def __init__(self) -> None:
        self.d = [P(4)]
        self.lst = [10, 20, 30]


def make() -> Own[list[P]]:
    return [P(5)]


def free_fn(d: Optional[list[P]]) -> int32:
    n = 0
    # free function: element field read off the unproven receiver
    n += d[0].x  # tpyc: warning(/Potential None access/)
    print("free print", d[0].x)  # tpyc: warning(/Potential None access/)
    return n


class M:
    k: int32

    def __init__(self) -> None:
        self.k = 1

    def meth(self, d: Optional[list[P]]) -> int32:
        # method: the same read beside a self field
        return self.k + d[0].x  # tpyc: warning(/Potential None access/)


def gen(d: Optional[list[P]]) -> Iterator[int32]:
    yield 0
    # generator: the receiver is a frame field
    yield d[0].x  # tpyc: warning(/Potential None access/)


async def coro(d: Optional[list[P]]) -> int32:
    await asyncio.sleep(0)
    # async: the receiver is a frame field read after a suspension
    return d[0].x  # tpyc: warning(/Potential None access/)


async def async_main() -> None:
    # annotated: an unannotated literal hits BUGS.md#async-optional-list-param-literal-array
    xs: list[P] = [P(3)]
    print("async", await coro(xs))


def closure(d: Optional[list[P]]) -> int32:
    def inner() -> int32:
        # closure: the receiver is a captured binding
        return d[0].x  # tpyc: warning(/Potential None access/)
    return inner()


class Ctor:
    k: int32

    def __init__(self, d: Optional[list[P]]) -> None:
        # constructor: a field init from the read
        self.k = d[0].x  # tpyc: warning(/Potential None access/)


def comprehension(d: Optional[list[P]]) -> Own[list[int32]]:
    # comprehension: the read in the element expression
    return [d[0].x for _ in range(2)]  # tpyc: warning(/Potential None access/)


def try_finally(d: Optional[list[P]]) -> int32:
    try:
        # try/finally: the read inside the try body
        return d[0].x  # tpyc: warning(/Potential None access/)
    finally:
        print("finally ran")


def match_arm(d: Optional[list[P]], k: int32) -> int32:
    match k:
        case 1:
            # match arm: the read inside a case body
            return d[0].x  # tpyc: warning(/Potential None access/)
        case _:
            return 0


def pointer_local(flag: bool) -> int32:
    d: Optional[list[P]] = None
    if flag:
        d = [P(13)]
    # pointer local: a reseated `T*` local, not a parameter
    return d[0].x  # tpyc: warning(/Potential None access/)


def field_recv(h: H) -> int32:
    # field receiver: the storage optional member is checked whole
    return h.d[0].x  # tpyc: warning(/Potential None access/)


# The warning is right: the section only reads through `d`; `Own` is spelled
# for its storage-optional parameter form, not to consume it.
def own_param(d: Own[list[P] | None]) -> int32:  # tpyc: warning(/param 'd' is never consumed/)
    # Own param: the rvalue-ref storage optional is checked whole
    return d[0].x  # tpyc: warning(/Potential None access/)


def readonly_param(d: readonly[list[P]] | None) -> int32:
    # readonly param: the const pointer is checked
    return d[0].x  # tpyc: warning(/Potential None access/)


def decl_alias(xs: list[Rec | None] | None) -> None:
    # decl: the element binds by pointer through the checked receiver ...
    r = xs[0]  # tpyc: warning(/Potential None access/)
    if r is not None:
        r.n = 9
    # ... so the write is visible through the container
    print("decl alias", xs[0].n)  # tpyc: warning(/Potential None access/)


def is_none(xs: list[Rec | None] | None) -> bool:
    # is-None test of an element off the unproven receiver
    return xs[0] is None  # tpyc: warning(/Potential None access/)


def truthy(xs: list[Rec | None] | None) -> int32:
    # truthiness of an element off the unproven receiver; parity holds only
    # because `Rec` has no `__bool__` (BUGS.md#optional-record-elem-truthiness-has-value)
    if xs[0]:  # tpyc: warning(/Potential None access/)
        return 1
    return 0


def str_elem(d: Optional[list[str]]) -> int32:
    # str element decl: owns a copy, as the plain-list and narrowed twins do
    # (a container element never lends a view)
    t = d[0]  # tpyc: warning(/Potential None access/) type(str)
    return len(t)


def bytes_elem(d: Optional[list[bytes]]) -> int32:
    # bytes element decl: owns a copy, like the str section above
    t = d[0]  # tpyc: warning(/Potential None access/) type(bytes)
    return len(t)


def owned_after_mutation(d: Optional[list[str]]) -> None:
    # str element decl whose receiver is mutated later: owns a copy
    t = d[0]  # tpyc: warning(/Potential None access/) type(str)
    d.clear()  # tpyc: warning(/Potential None access/)
    print("owned after mutation", t)


def loop_var_elem(rows: list[Optional[list[str]]]) -> None:
    t = ""
    for d in rows:
        # str element off a loop variable: owns a copy; a view would dangle
        # once clearing the iterable frees the list the loop variable borrowed
        t = d[0]  # tpyc: warning(/Potential None access/)
    rows.clear()
    print("loop var elem", t)


def index_read(h: H) -> int32:
    # index read: the checked receiver keeps the index normalisation
    return h.lst[-1]  # tpyc: warning(/Potential None access/)


def nested_field_elem(d: Optional[list[P]]) -> int32:
    # nested: an element of an element field
    return d[0].rows[1]  # tpyc: warning(/Potential None access/)


def nested_elem(d: Optional[list[list[int32]]]) -> int32:
    # nested: an element of an element
    return d[0][1]  # tpyc: warning(/Potential None access/)


def unpack_source(d: Optional[list[P]]) -> int32:
    # tuple unpack: an element field as the unpack source
    a, b = d[0].pair  # tpyc: warning(/Potential None access/)
    return a + b


def storage_loop_var(rows: list[list[int32] | None]) -> Own[list[int32]]:
    # storage-opt loop variable: the element optional is checked whole
    return [r[0] for r in rows]  # tpyc: warning(/Potential None access/)


def record_getitem(b: Optional[Bag]) -> int32:
    # a user record's `__getitem__` off the unproven receiver
    return b[0].x  # tpyc: warning(/Potential None access/)


def bytearray_read(b: Optional[bytearray]) -> int32:
    # a bytearray byte off the unproven receiver
    return b[1]  # tpyc: warning(/Potential None access/)


@dispatch
def trial[T, U](f: Fn[[T], U], xs: list[T]) -> int32:
    return 2


@dispatch
def trial[T, U](f: Fn[[Optional[T]], U], xs: list[T]) -> int32:
    return 1


def overload_trial() -> int32:
    rows: list[list[P]] = [[P(1)]]
    # overload trial: the Optional candidate's dry run analyzes `d[0]` with
    # an Optional `d` and is rejected at `len(d)`; the winner's `d` is a
    # plain list, so no check (and no warning) may survive from the trial
    return trial(lambda d: d[0].x + len(d), rows)  # tpyc: ok


G: Optional[list[P]] = make()


def global_slot() -> int32:
    # global: the slot pointer IS the nullable pointer (the module-level
    # statement face is `none_safety/panic_unproven_opt_container_elem_global`:
    # only a None global reaches a module-level read un-narrowed)
    return G[0].x  # tpyc: warning(/Potential None access/)


def main() -> None:
    # calls that print are bound first: print-argument evaluation order is
    # not what these sections test
    n = free_fn([P(1)])
    print("free", n)
    print("method", M().meth([P(2)]))
    for v in gen([P(6)]):
        print("gen", v)
    asyncio.run(async_main())
    print("closure", closure([P(8)]))
    print("ctor", Ctor([P(9)]).k)
    print("comprehension", comprehension([P(10)]))
    n = try_finally([P(11)])
    print("try", n)
    print("match", match_arm([P(12)], 1))
    print("pointer local", pointer_local(True))
    print("field", field_recv(H()))
    print("own", own_param(make()))
    print("readonly", readonly_param([P(14)]))
    decl_alias([Rec(4)])
    print("is none", is_none([Rec(4)]), is_none([None]))
    print("truthy", truthy([Rec(4)]), truthy([None]))
    print("str elem", str_elem(["ab"]))
    print("bytes elem", bytes_elem([b"xyz"]))
    owned_after_mutation(["long enough to live on the heap, not in SSO"])
    loop_var_elem([["long enough to live on the heap, not in SSO"]])
    print("index", index_read(H()))
    print("nested field elem", nested_field_elem([P(15)]))
    print("nested elem", nested_elem([[16, 17]]))
    print("unpack", unpack_source([P(18)]))
    print("storage loop var", storage_loop_var([[19], [20]]))
    print("record getitem", record_getitem(Bag()))
    print("bytearray", bytearray_read(bytearray(b"ab")))
    print("global", global_slot())
    print("overload trial", overload_trial())


main()
