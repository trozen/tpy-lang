# A `for` inside a resumable frame evaluates its iterable exactly ONCE: an
# rvalue source is kept in the frame for the loop's whole life, an lvalue
# source is captured by reference exactly as in a plain function, and an
# iterator OBJECT reached through a call on a param is held by reference.
# Every field of the loop is spelled off the source expression's own type
# (`decltype`), so a lazy combinator, a generator expression, a view of a
# readonly dict, a `T&` method on a const receiver and a conditional of two
# const params all take the frame route.
#
# Every section's loop body SUSPENDS. The lvalue sections mutate an element
# through the loop variable and the caller reads the change back, so a copy
# would be visible; the rvalue sections allocate between the yields, so a
# holder that died at the end of its state block would be disturbed. The
# accessor sections count their calls -- one, as CPython counts them.
from typing import Iterable, Iterator
from tpy import int32, Own, copy, readonly, auto_readonly
import asyncio


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Bag:
    cells: list[Cell]
    calls: int32

    def __init__(self) -> None:
        self.cells = [Cell(1), Cell(2)]
        self.calls = 0

    def items(self) -> list[Cell]:
        self.calls += 1
        return self.cells

    # A getter is readonly, so this section cannot count its calls; it
    # carries the property spelling of the lvalue capture instead.
    @property
    def view(self) -> list[Cell]:
        return self.cells

    def walk(self) -> Iterator[str]:
        # self.field source -- an lvalue, captured by reference.
        for c in self.cells:  # tpyc: ok
            c.v += 100
            yield "field " + str(c.v)

    @auto_readonly
    def items_m(self) -> list[Cell]:
        return self.cells

    def pairs(self, ys: list[int32]) -> Iterator[str]:
        # generator METHOD over a combinator of a field and a param; the
        # unpack target aliases the field's element, and the write through
        # it is what makes the receiver mutable (`ys` stays const: the
        # target's source is `zip`'s first argument alone).
        for c, y in zip(self.cells, ys):  # tpyc: ok
            c.v += y
            yield "method_zip " + str(c.v)
            churn()


class Counter:
    n: int32
    calls: int32

    def __init__(self, n: int32) -> None:
        self.n = n
        self.calls = 0

    def __iter__(self) -> "Counter":
        return self

    def __next__(self) -> int32:
        if self.n == 0:
            raise StopIteration()
        self.n -= 1
        return self.n


class Holder:
    it: Counter
    gets: int32

    def __init__(self, n: int32) -> None:
        self.it = Counter(n)
        self.gets = 0

    def get(self) -> Counter:
        self.gets += 1
        return self.it


class Cur:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> "Cur":
        return self

    def __next__(self) -> int32:
        if self.n == 0:
            raise StopIteration()
        self.n -= 1
        return self.n


class Deleg:
    inner: Cur

    def __init__(self, n: int32) -> None:
        self.inner = Cur(n)

    # lends its member iterator: a combinator over a Deleg keeps a reference
    # into the Deleg itself.
    def __iter__(self) -> Cur:
        return self.inner


class Row:
    cells: list[Cell]

    def __init__(self, a: int32, b: int32) -> None:
        self.cells = [Cell(a), Cell(b)]


def churn() -> int32:
    junk: list[list[int32]] = []
    for i in range(40):
        junk.append([i, i + 1, i + 2])
    return len(junk)


def make_cells() -> Own[list[Cell]]:
    return [Cell(10), Cell(20)]


def gen_cells() -> Iterator[Own[Cell]]:
    yield Cell(7)
    yield Cell(8)


def named_src() -> Iterator[str]:
    cells = [Cell(1), Cell(2)]
    # named local source -- an lvalue.
    for c in cells:  # tpyc: ok
        c.v += 100
        yield "named " + str(c.v)
    yield "named total " + str(cells[0].v + cells[1].v)


def param_src(cells: list[Cell]) -> Iterator[str]:
    # param source -- an lvalue; the caller sees the mutation.
    for c in cells:  # tpyc: ok
        c.v += 100
        yield "param " + str(c.v)


def subscript_src(table: dict[str, list[Cell]]) -> Iterator[str]:
    # container-ELEMENT subscript source -- an lvalue (one `__getitem__`).
    for c in table["a"]:  # tpyc: ok
        c.v += 100
        yield "subscript " + str(c.v)


def accessor_src(b: Bag) -> Iterator[str]:
    # impure borrow-returning method -- an lvalue, called once.
    for c in b.items():  # tpyc: ok
        c.v += 100
        yield "accessor " + str(c.v)


def property_src(b: Bag) -> Iterator[str]:
    # `@property` read -- the accessor spelling of the same lvalue.
    for c in b.view:  # tpyc: ok
        c.v += 100
        yield "property " + str(c.v)


def own_call_src() -> Iterator[str]:
    # `Own[list]` call -- an rvalue the frame owns.
    for c in make_cells():  # tpyc: ok
        yield "own_call " + str(c.v)
        yield "own_call churn " + str(churn())


def copy_src(cells: list[Cell]) -> Iterator[str]:
    # `copy(...)` -- an rvalue; mutating it must NOT reach the caller's list.
    for c in copy(cells):  # tpyc: ok
        c.v += 100
        yield "copy " + str(c.v)
        yield "copy churn " + str(churn())


def literal_src() -> Iterator[str]:
    # container literal -- an rvalue spelled with the slot's own type.
    for c in [Cell(4), Cell(5)]:  # tpyc: ok
        yield "literal " + str(c.v)
        yield "literal churn " + str(churn())


def slice_mut_src() -> Iterator[str]:
    cells = [Cell(1), Cell(2), Cell(3)]
    # slice of a MUTABLE source -- an rvalue view the frame holds.
    for c in cells[1:]:  # tpyc: ok
        c.v += 100
        yield "slice_mut " + str(c.v)
    yield "slice_mut total " + str(cells[1].v + cells[2].v)


def slice_const_src(cells: list[Cell]) -> Iterator[str]:
    # slice of a CONST source -- the slot type is deduced, not named.
    for c in cells[1:]:  # tpyc: ok
        yield "slice_const " + str(c.v)
        yield "slice_const churn " + str(churn())


def ternary_lvalues_src(flag: bool) -> Iterator[str]:
    xs = [Cell(1)]
    ys = [Cell(2)]
    # ternary of two LVALUES -- still an lvalue, captured by reference.
    for c in (xs if flag else ys):  # tpyc: ok
        c.v += 100
        yield "ternary_lv " + str(c.v)
    yield "ternary_lv total " + str(xs[0].v)


def ternary_temps_src(flag: bool) -> Iterator[str]:
    # ternary of two TEMPORARIES -- an rvalue; both arms are fresh lists.
    for c in ([Cell(10)] if flag else [Cell(11)]):  # tpyc: ok
        yield "ternary_tmp " + str(c.v)
        yield "ternary_tmp churn " + str(churn())


def walrus_src() -> Iterator[str]:
    # a WALRUS source denotes its target, so the loop aliases what `ws` holds
    # and a mutation through the loop var is visible through `ws` afterwards.
    for c in (ws := make_cells()):  # tpyc: ok
        c.v += 100
        yield "walrus " + str(c.v)
        yield "walrus churn " + str(churn())
    yield "walrus target " + str(ws[0].v + ws[1].v)


def str_temp_src(a: str, b: str) -> Iterator[str]:
    # a `str` temporary iterated by char -- an rvalue with value elements.
    for ch in a + b:  # tpyc: ok
        yield "str_tmp " + ch


def gen_call_src() -> Iterator[str]:
    # a generator CALL -- an rvalue frame embedded in this one.
    for c in gen_cells():  # tpyc: ok
        yield "gen_call " + str(c.v)
        yield "gen_call churn " + str(churn())


async def async_accessor(b: Bag) -> int32:
    total = 0
    # the async twin of the impure accessor -- one call.
    for c in b.items():  # tpyc: ok
        c.v += 100
        total += c.v
        await asyncio.sleep(0)
    return total


async def async_slice(cells: list[Cell]) -> int32:
    total = 0
    # the async twin of the const-source slice.
    for c in cells[1:]:  # tpyc: ok
        total += c.v
        await asyncio.sleep(0)
    return total


def gx_named() -> int32:
    xs = [1, 2, 3]
    # genexpr frame over a named source -- the constructor seeds from it.
    return sum(v * 2 for v in xs)  # tpyc: ok


def gx_own_call() -> int32:
    # genexpr frame over an `Own[list]` call -- the frame owns the source.
    return sum(c.v for c in make_cells())  # tpyc: ok


def gx_accessor(b: Bag) -> int32:
    # genexpr frame over an impure accessor -- called once.
    return sum(c.v for c in b.items())  # tpyc: ok


def zip_names_src(xs: list[int32], ys: list[int32]) -> Iterator[int32]:
    # lazy combinator over NAMES -- an rvalue iterator the frame owns.
    for a, b in zip(xs, ys):  # tpyc: ok
        yield a + b
        churn()


def enumerate_cells_src() -> Iterator[str]:
    cells = [Cell(1), Cell(2)]
    # combinator over a local NAME; the unpack target aliases the element,
    # so the mutation shows in the list afterwards.
    for i, c in enumerate(cells):  # tpyc: ok
        c.v += 100
        yield "enumerate " + str(i) + " " + str(c.v)
        churn()
    yield "enumerate total " + str(cells[0].v + cells[1].v)


def reversed_temp_src() -> Iterator[str]:
    # combinator over a TEMPORARY -- the frame owns the combinator, which
    # owns the list.
    for c in reversed(make_cells()):  # tpyc: ok
        yield "reversed_tmp " + str(c.v)
        yield "reversed_tmp churn " + str(churn())


def genexpr_src(xs: list[int32]) -> Iterator[int32]:
    # generator EXPRESSION at the head -- its frame is embedded in this one.
    for v in (x * 2 for x in xs):  # tpyc: ok
        yield v
        churn()


def genexpr_capture_src(xs: list[int32], k: int32) -> Iterator[int32]:
    # genexpr with a capture -- the embedded frame is a template over it.
    for v in (x + k for x in xs if x > 1):  # tpyc: ok
        yield v


def readonly_view_src(d: readonly[dict[str, Cell]]) -> Iterator[int32]:
    # view of a READONLY dict -- the slot takes the const view's own type.
    for c in d.values():  # tpyc: ok
        yield c.v
        churn()


def const_method_src(b: readonly[Bag]) -> Iterator[int32]:
    # `T&`-returning method on a CONST receiver -- a const_iterator pair.
    for c in b.items_m():  # tpyc: ok
        yield c.v
        churn()


def ternary_params_src(xs: list[Cell], ys: list[Cell], flag: bool) -> Iterator[int32]:
    # conditional of two CONST params -- const iterators, no copy. Read only:
    # a mutation through `c` is not traced to the params
    # (BUGS.md#frame-ternary-params-mutation-not-propagated).
    for c in (xs if flag else ys):  # tpyc: ok
        yield c.v
        churn()


def doubled(items: Iterable[int32]) -> Iterator[int32]:
    yield 0
    for x in items:
        yield x * 2


def delegate_proto_src(xs: list[int32]) -> Iterator[int32]:
    # delegating to a generator with a PROTOCOL-typed param: the embedded
    # frame is a template over the deduced argument type.
    yield -1
    for v in doubled(xs):  # tpyc: ok
        yield v


def iter_object_src(h: Holder) -> Iterator[int32]:
    # an iterator OBJECT reached through a call on a param -- held by
    # reference, so the accessor runs once and the object is drained.
    for v in h.get():  # tpyc: ok
        yield v
        churn()


def nested_src(rows: list[Row]) -> Iterator[str]:
    # inner source reads the OUTER loop var: the inner alias is spelled after
    # the outer loop var's field.
    for row in rows:  # tpyc: ok
        for c in row.cells:  # tpyc: ok
            c.v += 100
            yield "nested " + str(c.v)


def take(seed: Cell, n: int32) -> Own[list[Cell]]:
    return [Cell(seed.v + i) for i in range(n)]


def arg_temp_src() -> Iterator[str]:
    # an owned source whose ARGUMENT is a temporary the call does not keep:
    # the alias renders the argument as `std::declval`, the setup as itself.
    for c in take(Cell(5), 2):  # tpyc: ok
        yield "arg_temp " + str(c.v)
        yield "arg_temp churn " + str(churn())


def nested_combinator_src(xs: list[int32]) -> Iterator[int32]:
    # a combinator over a combinator: the inner rvalue is OWNED by the
    # outer, not seated on the frame.
    for i, v in enumerate(reversed(xs)):  # tpyc: ok
        yield i * 10 + v
        churn()


def combinator_temp_src() -> Iterator[int32]:
    # combinator over a TEMPORARY that lends its iterator: the temporary is
    # seated on the frame, not in the state block the combinator outlives.
    for i, v in enumerate(Deleg(3)):  # tpyc: ok
        yield i + v
        churn()


def str_literal_src() -> Iterator[str]:
    # a `str` LITERAL source -- the frame holds the view of its storage.
    for ch in "ab":  # tpyc: ok
        yield "str_lit " + ch
        yield "str_lit " + ch + "!"


def pairs_of(cells: list[Cell]) -> Iterator[tuple[int32, Cell]]:
    i = 0
    for c in cells:
        yield (i, c)
        i += 1


def next_unpack_src(cells: list[Cell]) -> Iterator[int32]:
    # unpack over a GENERATOR yielding a record member: the target aliases
    # the element, so the caller sees the mutation.
    for i, c in pairs_of(cells):  # tpyc: ok
        c.v += 10
        yield i + c.v


def shared_var_src(b: readonly[list[Cell]]) -> Iterator[int32]:
    a = [Cell(5)]
    # one loop var bound by two loops whose sources differ in const (a
    # readonly param, a local): its one field takes the join (const), and
    # both loops read through it. A mutation in the second loop would be a
    # C++ error, as it was under sema's marking before.
    for c in b:  # tpyc: ok
        yield c.v
    for c in a:  # tpyc: ok
        yield c.v


def own_param_iter_src(h: Own[Holder]) -> Iterator[int32]:  # tpyc: warning(/never consumed/)
    # an iterator object reached through a call on a BY-VALUE param: the
    # holder is a frame field, so the source is not held by reference and
    # the call re-renders at every advance
    # (BUGS.md#frame-iter-next-source-reevaluated). The count is not printed.
    for v in h.get():  # tpyc: ok
        yield v


async def async_combinator_temp() -> int32:
    total = 0
    # the async twin of the seated combinator temporary.
    for i, v in enumerate(Deleg(3)):  # tpyc: ok
        total += i + v
        await asyncio.sleep(0)
    return total


async def async_zip(xs: list[int32], ys: list[int32]) -> int32:
    total = 0
    # the async twin of the combinator source.
    for a, b in zip(xs, ys):  # tpyc: ok
        total += a * b
        await asyncio.sleep(0)
    return total


async def async_genexpr(xs: list[int32]) -> int32:
    total = 0
    # the async twin of the genexpr source.
    for v in (x * 3 for x in xs):  # tpyc: ok
        total += v
        await asyncio.sleep(0)
    return total


def drain(it: Iterator[str]) -> None:
    for s in it:
        print(s)


def main() -> None:
    drain(named_src())

    pcells = [Cell(1), Cell(2)]
    drain(param_src(pcells))
    print("param after", pcells[0].v, pcells[1].v)

    b0 = Bag()
    for s in b0.walk():
        print(s)
    print("field after", b0.cells[0].v, b0.cells[1].v)

    table: dict[str, list[Cell]] = {"a": [Cell(1), Cell(2)]}
    drain(subscript_src(table))
    print("subscript after", table["a"][0].v, table["a"][1].v)

    b1 = Bag()
    drain(accessor_src(b1))
    print("accessor after", b1.cells[0].v, "calls", b1.calls)

    b2 = Bag()
    drain(property_src(b2))
    print("property after", b2.cells[0].v, b2.cells[1].v)

    drain(own_call_src())

    ccells = [Cell(1), Cell(2)]
    drain(copy_src(ccells))
    print("copy after", ccells[0].v, ccells[1].v)

    drain(literal_src())
    drain(slice_mut_src())
    drain(slice_const_src([Cell(1), Cell(2), Cell(3)]))
    drain(ternary_lvalues_src(True))
    drain(ternary_temps_src(True))
    drain(walrus_src())
    drain(str_temp_src("ab", "c"))
    drain(gen_call_src())

    b3 = Bag()
    print("async_accessor", asyncio.run(async_accessor(b3)),
          "calls", b3.calls)
    print("async_slice", asyncio.run(async_slice([Cell(1), Cell(2), Cell(3)])))

    print("gx_named", gx_named())
    print("gx_own_call", gx_own_call())
    b4 = Bag()
    print("gx_accessor", gx_accessor(b4), "calls", b4.calls)

    print("zip_names", list(zip_names_src([1, 2, 3], [10, 20])))
    drain(enumerate_cells_src())
    drain(reversed_temp_src())
    print("genexpr", list(genexpr_src([1, 2, 3])))
    print("genexpr_capture", list(genexpr_capture_src([1, 2, 3], 10)))
    rd = {"a": Cell(5), "b": Cell(6)}
    print("readonly_view", list(readonly_view_src(rd)))
    b5 = Bag()
    print("const_method", list(const_method_src(b5)))
    print("ternary_params", list(ternary_params_src([Cell(1)], [Cell(2)], False)))
    print("delegate_proto", list(delegate_proto_src([1, 2])))
    h = Holder(3)
    print("iter_object", list(iter_object_src(h)), "gets", h.gets, "left", h.it.n)
    rows = [Row(1, 2), Row(3, 4)]
    drain(nested_src(rows))
    print("nested after", rows[1].cells[1].v)
    b6 = Bag()
    for s in b6.pairs([5, 6]):
        print(s)
    print("method_zip after", b6.cells[0].v, b6.cells[1].v)
    drain(arg_temp_src())
    print("nested_combinator", list(nested_combinator_src([1, 2])))
    print("combinator_temp", list(combinator_temp_src()))
    drain(str_literal_src())
    ncells = [Cell(1), Cell(2)]
    print("next_unpack", list(next_unpack_src(ncells)), ncells[0].v, ncells[1].v)
    print("shared_var", list(shared_var_src([Cell(6)])))
    print("own_param_iter", list(own_param_iter_src(Holder(2))))
    print("async_combinator_temp", asyncio.run(async_combinator_temp()))
    print("async_zip", asyncio.run(async_zip([1, 2], [3, 4])))
    print("async_genexpr", asyncio.run(async_genexpr([1, 2])))


main()
