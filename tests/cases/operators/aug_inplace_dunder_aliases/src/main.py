# In-place `op=` matches CPython: a dunder updates the shared object, a value
# rebinds; a dunder's operand is that call's argument (one section warns a copy).
from typing import Iterator
from tpy import Own, String, ValueType, dispatch, int8, int32, nocopy
from tplib import ArrayList


class A:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, k: int32) -> "A":
        self.n += k
        return self


class P(ValueType):
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[P]":
        return P(self.n + k)


def record_iadd() -> None:
    a = A(1)
    b = a
    # record with `__iadd__`: updated in place, as in CPython
    a += 10  # tpyc: ok
    print("iadd", a.n, b.n, a is b)


def list_iadd() -> None:
    xs = [1, 2]
    ys = xs
    # list `+=` extends the shared list
    xs += [3]  # tpyc: ok
    print("list", xs, ys, xs is ys)


def bytearray_iadd() -> None:
    ba = bytearray(b"ab")
    al = ba
    # bytearray `+=` extends the shared buffer
    ba += b"c"  # tpyc: ok
    print("bytearray", ba, al, ba is al)


def int_iadd() -> None:
    n = 1
    m = n
    # int `+=` rebinds
    n += 10  # tpyc: ok
    print("int", n, m)


def str_iadd() -> None:
    s = "ab"
    t = s
    # str `+=` rebinds
    s += "c"  # tpyc: ok
    print("str", s, t)


def string_iadd() -> None:
    s = String("ab")
    t = s
    # String `+=` appends in place; a String binding is its own value
    s += "c"  # tpyc: ok
    print("String", s, t)


def valuetype_add() -> None:
    a = P(1)
    b = a
    # a ValueType record with only `__add__`: a value, so in place is the rebind
    a += 10  # tpyc: ok
    print("valuetype", a.n, b.n)


class Bump:
    def __init__(self, k: int32) -> None:
        self.k = k


class Acc:
    kept: list[Bump]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.kept = []

    @dispatch
    def __iadd__(self, b: Bump) -> "Acc":
        self.n += b.k
        return self

    @dispatch
    def __iadd__(self, b: bool) -> "Acc":
        self.n += 100
        return self

    def __isub__(self, xs: list[int32]) -> "Acc":
        self.n -= len(xs)
        return self

    def __imul__(self, b: Own[Bump]) -> "Acc":
        self.kept.append(b)
        return self


class Small:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, k: int8) -> "Small":
        self.n += k
        return self


class Holder:
    acc: Acc

    def __init__(self) -> None:
        self.acc = Acc(0)

    def add(self, b: Bump) -> None:
        # a record operand in a method body, on a field target
        self.acc += b  # tpyc: ok
        print("operand_method", self.acc.n)


def operand_gen(b: Bump) -> Iterator[int32]:
    a = Acc(1)
    # a record operand in a generator body
    a += b  # tpyc: ok
    yield a.n
    b.k = 10
    a += b  # tpyc: ok
    yield a.n


# A borrow `yield xs` lends `xs` until the consumer's next step, so the
# generator may move it afterwards; each consumer reads within its step.
def yield_then_iadd() -> Iterator[list[int32]]:
    xs = [1, 2]
    yield xs
    a = [0]
    a += xs  # tpyc: ok
    yield a


@nocopy
class Cell:
    def __init__(self, n: int32) -> None:
        self.n = n


def yield_or_move(flag: bool) -> Iterator[list[Cell]]:
    xs = [Cell(7)]
    if flag:
        yield xs
    else:
        # the branch that does not yield moves the elements out
        a: list[Cell] = []
        a += xs  # tpyc: ok
        yield a


def yield_then_move_records() -> Iterator[list[Bump]]:
    bs = [Bump(1)]
    yield bs
    # the step after the yield moves the records out, uncopied
    out: list[Bump] = []
    out += bs  # tpyc: ok
    yield out


def yield_sections() -> None:
    for got in yield_then_iadd():
        print("yield_iadd", got)
    for cells in yield_or_move(False):
        print("yield_branch", len(cells), cells[0].n)
    for recs in yield_then_move_records():
        print("yield_records", [b.k for b in recs])


def make_list(b: Own[Bump]) -> Own[list[int32]]:
    kept = [b]
    return [kept[0].k]


def iterable_operands() -> None:
    a = [0]
    # a range and an iterator call at the list's Iterable parameter
    a.extend(range(3))  # tpyc: ok
    a += range(2)  # tpyc: ok
    bs = [Bump(1), Bump(2)]
    xs = [Bump(0)]
    xs.extend(reversed(bs))  # tpyc: warning(/copies Bump elements/)
    # `bs`'s last use still copies through `reversed`
    # (BUGS.md#iterator-call-last-use-copies)
    xs += reversed(bs)  # tpyc: warning(/copies Bump elements/)
    # a call whose own `Own[Bump]` argument takes a copy of a live local
    b = Bump(7)
    a += make_list(b)  # tpyc: warning(/copies Bump into owned storage/)
    a.extend(make_list(b))  # tpyc: warning(/copies Bump into owned storage/)
    print("iterable_operands", a, [x.k for x in xs], b.k)


class Slots:
    items: list[Bump]

    def __init__(self) -> None:
        self.items = [Bump(0)]

    def __setitem__(self, i: int32, v: Own[Bump]) -> None:
        self.items[i] = v


def setitem_own_value() -> None:
    sl = Slots()
    b = Bump(5)
    # the `__setitem__` value at an `Own[Bump]` parameter: a copy of a live
    # local, warned (CPython aliases; `b` is not mutated afterwards)
    sl[0] = b  # tpyc: warning(/copies Bump into container/)
    print("setitem_own", sl.items[0].k, b.k)


def operand_narrow_int() -> None:
    v = Small(1)
    # an int literal at an `int8` parameter (300 is refused, as in the call)
    v += 3  # tpyc: ok
    print("operand_narrow_int", v.n)


def operand_record() -> None:
    a = Acc(1)
    b = Bump(2)
    # a record of another class as the operand
    a += b  # tpyc: ok
    b.k = 50
    a += b  # tpyc: ok
    print("operand_record", a.n, b.k)


def operand_list_literal() -> None:
    a = Acc(10)
    # a list literal into a `list[int32]` parameter
    a -= [1, 2, 3]  # tpyc: ok
    print("operand_list_literal", a.n)


def operand_own() -> None:
    a = Acc(0)
    b = Bump(7)
    # an `Own[Bump]` parameter takes a COPY of a live local (warned; CPython
    # aliases, so the section does not mutate `b` afterwards)
    a *= b  # tpyc: warning(/copies Bump into owned storage/)
    print("operand_own", a.kept[0].k, b.k)


def operand_rebound(flag: bool) -> None:
    a = Acc(1)
    b = Bump(2)
    if flag:
        b = Bump(3)
    # a rebound record local: the `Bump` variant, not `bool`
    a += b  # tpyc: ok
    print("operand_rebound", a.n)


def operand_narrowed(flag: bool) -> None:
    a = Acc(1)
    o: Bump | None = None
    if flag:
        o = Bump(4)
    if o is not None:
        # a narrowed Optional operand
        a += o  # tpyc: ok
    print("operand_narrowed", a.n)


class Row:
    def __init__(self, x: int32) -> None:
        self.x = x


class ListSlots:
    data: list[list[int32]]

    def __init__(self) -> None:
        self.data = [[0]]

    def __setitem__(self, i: int32, v: Own[list[int32]]) -> None:
        self.data[i] = v


def setitem_copies() -> None:
    al = ArrayList[Row, 4]()
    al.append(Row(1))
    z = Row(2)
    # a live record at a library `Own[T]` element slot: copied, warned
    al[0] = z  # tpyc: warning(/copies Row into container/)
    print("setitem_arraylist", al[0].x, z.x)
    sl = ListSlots()
    v = [7, 8]
    # a live list at an `Own[list[int32]]` value parameter: copied, warned
    sl[0] = v  # tpyc: warning(/copies list\[int32\] into container/)
    print("setitem_list", sl.data[0], v)


def main() -> None:
    record_iadd()
    list_iadd()
    bytearray_iadd()
    int_iadd()
    str_iadd()
    string_iadd()
    valuetype_add()
    operand_record()
    operand_list_literal()
    operand_own()
    operand_rebound(True)
    operand_rebound(False)
    operand_narrowed(True)
    operand_narrowed(False)
    Holder().add(Bump(4))
    for n in operand_gen(Bump(2)):
        print("operand_gen", n)
    yield_sections()
    operand_narrow_int()
    iterable_operands()
    setitem_own_value()
    setitem_copies()


main()
