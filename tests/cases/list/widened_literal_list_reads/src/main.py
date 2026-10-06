# An unannotated list literal has one element type that every use follows,
# whichever use widened it (docs/LANGUAGE_FEATURES.md "List Literal Inference").
import asyncio
from typing import Iterator
from tpy import (float32, int8, int32, int64, dispatch, Equatable, Own,
                 ReturnException, Span, error_return)


def big(v: list[int64]) -> None:
    v.append(5000000000)


def same(v: list[int32]) -> None:
    v.append(7)


def hold8(v: list[int8]) -> None:
    v.append(7)


def w64() -> int64:
    return 5000000000


def show(n: int64) -> int64:
    return n + 1


def neg(x: int64) -> int64:
    return -x


def flag() -> bool:
    return len(str(1)) == 1


def take_any(v: list[int8] | list[int64]) -> None:
    pass


def grow(*xs: list[int64]) -> None:
    for x in xs:
        x.append(w64())


def pair_sum(t: tuple[list[int64], int64]) -> int64:
    a, b = t
    a.append(b)
    return b + a[0]


class Wide(Exception):
    total: int64

    def __init__(self, xs: list[int64]) -> None:
        super().__init__()
        xs.append(w64())
        self.total = xs[0] + xs[-1]


def pick(f: bool) -> Own[list[int64]]:
    ys: list[int64] = [1]
    zs: list[int64] = [2]
    # function: a select returned as Own copies the local it picks instead of
    # moving it, warned (BUGS.md#reference-ternary-position-gaps).
    return ys if f else zs  # tpyc: warning(/copies list\[int64\] into owned storage/)


def has_item[T: Equatable](xs: list[T], v: T) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


@dispatch
def total(v: Span[int32]) -> int64:
    t: int64 = 0
    for x in v:
        t += x
    return t


@dispatch
def total(v: str) -> int64:
    return len(v)


def param_widens() -> None:
    ys = [1]
    # function: reads placed before the call that widens the list.
    n = ys[0]  # tpyc: type(int64)
    m = ys[0] + w64()  # tpyc: type(int64)
    print("fn.print_before", ys[0])
    big(ys)
    # function: reads after it -- locals, an argument, a comparison,
    # arithmetic and whole-list consumers.
    k = ys[1]  # tpyc: type(int64)
    a: int64 = ys[1]  # tpyc: ok
    print("fn.before", n, m)
    print("fn.after", k, a, show(ys[1]), ys[1] > 4000000000)  # tpyc: ok
    print("fn.whole", ys[1] + 1, sum(ys), sorted(ys))
    for v in ys:  # tpyc: ok
        print("fn.loop", v)
    for i, v in enumerate(ys):  # tpyc: ok
        print("fn.enumerate", i, v)
    print("fn.pop", ys.pop(), len(ys))


def param_confirms() -> None:
    ys = [1]
    n = ys[0]  # tpyc: type(int32)
    # function: a parameter of the element's own width confirms it.
    same(ys)  # tpyc: ok
    k = ys[1]  # tpyc: type(int32)
    print("fn.confirm", n, k, ys)


def stores() -> None:
    ys = [1, 2]
    n = ys[0]  # tpyc: type(int64)
    # function: insert of a typed value widens, like append.
    ys.insert(0, w64())  # tpyc: ok
    zs = [1, 2]
    k = zs[0]  # tpyc: type(int)
    # function: a rebinding to a wider literal is one more value held.
    zs = [5000000000, 3, 4]  # tpyc: warning(/outside default int32 range/)
    vs = [1]  # tpyc: type(list[int])
    m = vs[0]  # tpyc: type(int)
    # function: a literal that extend or += adds is one more value held.
    vs.extend([5000000000])  # tpyc: warning(/outside default int32 range/)
    ws = [1]  # tpyc: type(list[int])
    ws += [5000000000]  # tpyc: warning(/outside default int32 range/)
    print("fn.insert", ys, n)
    print("fn.rebind", zs, k)
    print("fn.extend", vs, m, ws, ws[1] + 1)


def first_binding() -> None:
    a: int8 = 5
    ys = [a]
    # function: typed values in the first binding decide the element there,
    # as for a numeric local; a literal stored later adapts to it.
    ys.append(7)  # tpyc: ok
    n = ys[1]  # tpyc: type(int8)
    # function: an operand the element does not hold counts as its own type.
    wide = ys[0] + 300  # tpyc: type(int32)
    f: float32 = 1.5
    fs = [f]
    fs.append(0.25)  # tpyc: ok
    x = fs[1]  # tpyc: type(float32)
    zs = [1]
    # function: a literal-only first binding starts at the default type, and
    # a typed value stored later only widens it.
    zs.append(a)  # tpyc: ok
    k = zs[1]  # tpyc: type(int32)
    print("fn.first_binding", ys, n, wide, fs, x, zs, k)


def union_param() -> None:
    xs = [1, 2]
    # function: the union member that admits the list is the slot it goes to.
    take_any(xs)  # tpyc: ok
    xs.append(w64())
    print("fn.union", xs, xs[-1] + 1)


def seeded_local() -> None:
    x = 1
    ys = [x]
    # function: a literal-seeded local seeds the list as a literal does: the
    # list widens with a later store, and the local keeps its own type.
    ys.append(w64())  # tpyc: ok
    n = ys[0]  # tpyc: type(int64)
    print("fn.seeded_local", x, ys, n)


def typed_seeded() -> None:
    a: int8 = 5
    b: int8 = 100
    ys = [a, 1]
    # function: a literal beside the typed value adapts, as does one stored
    # later; the element stays what the first binding gave.
    ys[1] = b  # tpyc: ok
    ys.append(-128)  # tpyc: ok
    n = ys[2]  # tpyc: type(int8)
    m = ys[0] * 300  # tpyc: type(int32)
    print("fn.typed_seeded", ys, n, m)


def generic_call() -> None:
    n: int = 2
    nums = [1, 2]
    # function: another argument binds T, and the list takes that element.
    print("fn.generic", has_item(nums, n))  # tpyc: ok
    nums.append(50000000000000000000)  # tpyc: ok
    print("fn.generic", nums)
    a = [3, 1, 4]
    k = a[0]  # tpyc: type(int64)
    # function: the key function's parameter binds T the same way.
    print("fn.sorted_key", sorted(a, key=neg), k + w64(), a[1] + w64())  # tpyc: ok
    ys = [1, 2]
    # function: a call with several candidates decides the element first.
    print("fn.overloaded", total(ys), total("ab"))  # tpyc: ok


def typed_slots() -> None:
    xs = [1, 2]
    xs.append(w64())
    # function: a tuple element declared list[int64] is a typed container.
    t: tuple[list[int64], int32] = (xs, 1)  # tpyc: ok
    a, k = t
    print("fn.tuple", sum(a), k)
    outer: list[list[int64]] = []
    ws = [1, 2]
    j = ws[1]  # tpyc: type(int64)
    # function: so is the element of a list of lists; the store copies.
    outer.append(ws)  # tpyc: warning(/copies/)
    outer[0].append(5000000000)
    print("fn.nested", outer, j)
    ys = [1]
    n = ys[0]  # tpyc: type(int64)
    # function: each argument packed into *args meets the declared element.
    grow(ys, ys)  # tpyc: ok
    ys.append(7)
    print("fn.star_args", ys, n)


def select_operands() -> None:
    ys = [1]
    ws = [2]
    # function: an and/or operand under a declared target is a value the
    # target receives.
    zs: list[int64] = ys or [w64()]  # tpyc: ok
    ys.append(w64())
    # function: so is a ternary arm.
    vs: list[int64] = ws if flag() else ys  # tpyc: ok
    ws.append(w64())
    print("fn.select", ys, len(zs), zs[-1] + 1, ws, len(vs), vs[-1] + 1)
    xs = [1]
    # function: a generator expression beside the list in one tuple argument
    # does not decide the list.
    print("fn.genexpr", pair_sum((xs, sum(w64() + y for y in range(1)))))  # tpyc: ok
    xs.append(w64())
    print("fn.genexpr", xs)


class Holder:
    v: list[int64]

    def __init__(self) -> None:
        self.v = []


class Maker:
    base: int64
    last: int64

    def __init__(self, base: int64) -> None:
        ys = [1]
        # constructor: appending a typed value widens the element.
        ys.append(base)  # tpyc: ok
        self.base = ys[1]  # tpyc: ok
        self.last = sum(ys)

    def make(self) -> Own[list[int64]]:
        ys = [1]
        # method: the declared return type widens the element.
        n = ys[0]  # tpyc: type(int64)
        ys.append(n + self.base)
        return ys

    def fill(self, h: Holder) -> None:
        ys = [1, 2]
        n = ys[0]  # tpyc: type(int64)
        ys.append(3)
        # method: a typed field is a typed container. The store copies the
        # list (warned), so the mutation and the read go through the field.
        h.v = ys  # tpyc: warning(/copies/)
        h.v.append(self.base)
        print("method.field", h.v, n)


def raise_arg() -> None:
    zs = [1]
    n = zs[0]  # tpyc: type(int64)
    try:
        # raise: the exception's parameter is a typed slot.
        raise Wide(zs)  # tpyc: ok
    except Wide as e:
        print("raise", e.total, zs, n)


def gen() -> Iterator[int]:
    ys = [1]
    # generator: a literal no default int holds makes the element an int.
    ys.append(5000000000)  # tpyc: warning(/outside default int32 range/)
    for v in ys:
        yield v
    yield ys[1] + 1


def gen_lists() -> Iterator[Own[list[int64]]]:
    xs = [1, 2]
    n = xs[0]  # tpyc: type(int64)
    xs.append(n + w64())
    # generator: the declared yield type is a typed container.
    yield xs  # tpyc: ok


async def fill(n: int32) -> int64:
    xs = [0] * n
    # async: a store through a subscript widens the element.
    xs[1] = w64()  # tpyc: ok
    await asyncio.sleep(0)
    return xs[1] + xs[0] + len(xs)


def comprehension() -> None:
    ys = [1, 2]
    ys[0] += w64()  # tpyc: ok
    # comprehension: iterates the list an augmented store widened.
    zs = [v + 1 for v in ys]  # tpyc: ok
    print("comp", zs, ys[0])


def closure() -> None:
    ys = [1]
    big(ys)

    def total_ends() -> int64:
        # closure: reads the list after the call that widened it.
        return ys[0] + ys[len(ys) - 1]  # tpyc: ok

    ys.append(7)
    print("closure", total_ends(), len(ys))


def match_arm(kind: int32) -> None:
    a: int8 = 5
    match kind:
        case 1:
            ys = [a]
            # match arm: a list of typed values is decided at its first
            # binding; a parameter of that element type confirms it.
            hold8(ys)  # tpyc: ok
            print("match", sum(ys), ys[0] + ys[1])
        case _:
            print("match", 0)


def try_finally() -> None:
    fs = [1.5]
    try:
        # try/finally: a float list keeps its family and follows its stores.
        x = fs[0] * 2  # tpyc: type(float)
        fs.append(x)
    finally:
        fs[0] += 0.25
    print("try", fs, sum(fs))


class Scope:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> bool:
        return False


def with_body(c: bool) -> None:
    with Scope():
        if c:
            ys = [1]
        else:
            ys = [2, 3]
        # with body: sibling arms bind one list of different sizes.
        big(ys)  # tpyc: ok
        print("with", sum(ys), ys[0])


class Refused(Exception, ReturnException):
    pass


@error_return(Refused)
def alias_pair(limit: int64) -> int64:
    ys = [1]
    zs = ys
    # error_return: two names, one list; widened and mutated through the
    # alias, read through the first name.
    big(zs)  # tpyc: ok
    if ys[1] < limit:
        raise Refused
    return ys[1] + len(ys)


def lookups_pos() -> None:
    xs = [1, 2, 3, 2]  # tpyc: type(list[int64])
    a: int8 = 3
    # function: remove / index / count and `in` look the value up: a literal
    # adapts and a narrower typed value converts, and the element stays open.
    xs.remove(1)  # tpyc: ok
    n = xs.count(2)
    i = xs.index(a)
    print("fn.lookups.in", 2 in xs, a in xs, 7 not in xs)
    xs.append(w64())
    print("fn.lookups", xs, n, i, w64() in xs)


def truth_pos(c: bool) -> None:
    xs = [1, 2]  # tpyc: type(list[int64])
    ys = [3]  # tpyc: type(list[int64])
    # function: a truth test reads the length only, and so does a list
    # operand of an and / or that is one.
    if xs:  # tpyc: ok
        print("fn.truth.if", len(xs))
    if ys and c:  # tpyc: ok
        print("fn.truth.and", len(ys))
    xs.append(w64())
    ys.append(w64())
    print("fn.truth", xs, ys)


def wide_lookups_pos(n: int64, big: int) -> None:
    ps = [2, 3, 5]  # tpyc: type(Array[int32, 3])
    k = {1: "x", 2: "y"}  # tpyc: type(dict[int32, str])
    s = {1, 2}  # tpyc: type(set[int32])
    xs = [1, 2]  # tpyc: type(Array[int32, 2])
    # function: a value wider than the element decides the container first
    # and is compared as it is, never converted down.
    print("fn.wide.in", n in ps)  # tpyc: ok
    print("fn.wide.bigint", big in k, big in s, big in xs)  # tpyc: ok


def main() -> None:
    param_widens()
    param_confirms()
    stores()
    first_binding()
    typed_seeded()
    seeded_local()
    union_param()
    generic_call()
    typed_slots()
    select_operands()
    lookups_pos()
    truth_pos(True)
    wide_lookups_pos(3, 2)
    wide_lookups_pos(4, 1000000000000)
    print("fn.pick", pick(True), pick(False))
    raise_arg()
    m = Maker(5000000000)
    print("ctor", m.base, m.last)
    r = m.make()
    r.append(7)
    print("method", r)
    h = Holder()
    m.fill(h)
    for v in gen():
        print("gen", v)
    for g in gen_lists():
        g.append(7)
        print("gen.lists", len(g), g[2] + 1, g[3])
    print("async", asyncio.run(fill(3)))
    comprehension()
    closure()
    match_arm(1)
    try_finally()
    with_body(True)
    with_body(False)
    try:
        print("error_return", alias_pair(1))
    except Refused:
        print("error_return refused")


main()
