# What a call's result may point into, followed through the call, one section
# per position, callees of another module included. A warned section reads
# the binding it invalidates only where the write stores an equal value
# (field: BUGS.md#field-off-call-unkeyed).
import asyncio
from typing import Callable, Iterator, overload

from tpy import Own, StrView, int32

from lib import Cell, Chooser, Crate, opt_cell


class Inner:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n += 1

    def peek(self) -> int32:
        return self.n

    def me(self) -> "Inner":
        return self


def ident(x: Inner) -> Inner:
    return x


class Holder:
    inner: Inner
    other: Inner

    def __init__(self) -> None:
        self.inner = Inner()
        self.other = Inner()

    def get(self) -> Inner:
        return self.inner

    def pick(self, x: Inner, y: Inner, c: bool) -> Inner:
        return x if c else y


class Outer:
    h: Holder

    def __init__(self) -> None:
        self.h = Holder()

    # lend: method -- the free call hands back `f.inner`, written through.
    def lend_write(self) -> None:
        f = self.h
        ident(f.inner).bump()  # tpyc: ok

    # lend: method -- binding the result is a mutable use of the argument.
    def lend_bind(self) -> int32:
        f = self.h
        x = ident(f.inner)  # tpyc: ok
        return x.peek()

    # lend: method -- every operand the call may hand back is credited.
    def lend_multi(self, c: bool) -> None:
        f = self.h
        g = f.inner
        other = f.other
        f.pick(g, other, c).bump()  # tpyc: ok

    # lend: generator method -- the write through the result runs in the frame.
    def lend_gen(self) -> Iterator[int32]:
        f = self.h
        ident(f.inner).bump()  # tpyc: ok
        yield f.inner.n

    # lend: async method
    async def lend_async(self) -> int32:
        f = self.h
        ident(f.inner).bump()  # tpyc: ok
        return f.inner.n


class Built:
    n: int32

    # lend: constructor
    def __init__(self, h: Holder) -> None:
        ident(h.inner).bump()  # tpyc: ok
        self.n = h.inner.n


# lend: free function -- the argument is mutated through the call's result.
def lend_free(h: Holder) -> None:
    ident(h.inner).bump()  # tpyc: ok


# lend: closure -- a nested def writes through the call over a capture.
def lend_closure(h: Holder) -> None:
    def go() -> None:
        ident(h.inner).bump()  # tpyc: ok
    go()


class Jar:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Mid:
    jar: Jar

    def __init__(self, x: int32) -> None:
        self.jar = Jar(x)


class H:
    mid: Mid

    def __init__(self) -> None:
        self.mid = Mid(1)

    def peek(self) -> Mid:
        return self.mid

    # field: method -- the read itself stays BUGS.md#field-off-call-unkeyed.
    def field_meth(self) -> int32:
        j = self.peek().jar
        self.mid = Mid(1)  # tpyc: warning(/Mutation of 'self' while borrowed/)
        return j.x


# field: free function -- `j` borrows `h` through `peek`.
def field_free(h: H) -> int32:
    j = h.peek().jar
    h.mid = Mid(1)  # tpyc: warning(/Mutation of 'h' while borrowed/)
    return j.x


# field: closure
def field_closure(h: H) -> int32:
    def go() -> int32:
        j = h.peek().jar
        h.mid = Mid(1)  # tpyc: warning(/Mutation of 'h' while borrowed/)
        return j.x
    return go()


class FieldCtor:
    n: int32

    # field: constructor
    def __init__(self, h: H) -> None:
        j = h.peek().jar
        h.mid = Mid(1)  # tpyc: warning(/Mutation of 'h' while borrowed/)
        self.n = j.x


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick_box(b: Box) -> Box:
    return b


# select: function -- the returned select's call arm lends `a`; reading `r`
# after the growth is BUGS.md#returned-select-call-arm-no-lender.
def select_fn(a: Box, b: Box, c: bool) -> Box:
    return pick_box(a) if c else b


class Picker:
    def pick(self, b: Box) -> Box:
        return b

    # select: method
    def select_meth(self, a: Box, b: Box, c: bool) -> Box:
        return self.pick(a) if c else b


# A read of a binding the append may have invalidated runs only when `read`.
def select_sections(read: bool) -> None:
    xs = [Box(1)]
    five = Box(5)
    r = select_fn(xs[0], five, True)
    xs.append(Box(2))  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    if read:
        print("select fn", r.n)
    ys = [Box(1)]
    pk = Picker()
    other = Box(5)
    s = pk.select_meth(ys[0], other, True)
    ys.append(Box(2))  # tpyc: warning(/Mutation of 'ys' while borrowed/)
    if read:
        print("select meth", s.n)
    print("select", len(xs), len(ys))


# chain: a chain of lending calls borrows its first receiver.
def chain(h: Holder, read: bool) -> int32:
    q = h.get().me()  # tpyc: ok
    h.inner = Inner()  # tpyc: warning(/Mutation of 'h' while borrowed/)
    if read:
        q.bump()
    return h.inner.n


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


# walrus: a generator kept across passes is traced through the walrus source.
def walrus_rows(rows: list[list[int32]]) -> Iterator[int32]:
    for i in range(3):
        g = items((r := rows[0]))  # tpyc: ok
        yield first(g) + len(r)


class Named:
    name: str

    def __init__(self) -> None:
        self.name = "named"


def view_of(h: Named) -> StrView:
    return h.name


def callable_view(f: Callable[[Named], StrView], read: bool) -> int32:
    # callable: a function value's view result, held as a view, lends its
    # argument (an unannotated local would own a copy).
    h = Named()
    q: StrView = f(h)
    n = len(q)
    h.name = "replaced"  # tpyc: warning(/Mutation of 'h' while borrowed/)
    if read:
        n = len(q)
    return n


class Tagged:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s


def tag_first(t: tuple[str, Tagged]) -> Tagged:
    v = t[1]
    s0: StrView = t[0]
    v.s = s0
    return v


def relay_tag(v: Tagged) -> Tagged:
    return v


def temp_nested(s: str) -> None:
    # temp: an inner call's tuple temporary backs the outer call's result.
    v = Tagged("static")
    q = relay_tag(tag_first((s, v)))  # tpyc: warning(/Result borrows from temporary argument 't'/)
    print("temp nested")


def opt_inner(h: Holder) -> Inner | None:
    return h.inner


def keep_boxes(bs: Own[list[Box]]) -> Own[list[Box]]:
    return bs


def overload_move() -> None:
    # overload: a `typing.overload` callee defined below lends its argument
    # before its implementation is analyzed, so handing the list on copies it.
    bs = [Box(1), Box(2)]
    b = first_box(bs, 0)
    kept = keep_boxes(bs)  # tpyc: warning(/copies list\[Box\] into owned storage/)
    # Mutated after the boundary: `b` still reaches the list's element.
    b.n += 10
    print("overload", b.n, len(kept))


@overload
def first_box(bs: list[Box]) -> Box: ...
@overload
def first_box(bs: list[Box], i: int32) -> Box: ...
def first_box(bs: list[Box], i: int32 = 0) -> Box:
    return bs[i]


class ShelfUser:
    # overload: method -- a later record's `typing.overload` method lends its
    # argument through the stub that receives the implementation's facts.
    def run(self, sh: "Shelf") -> None:
        bs = [Box(1), Box(2)]
        b = sh.get(bs)
        kept = keep_boxes(bs)  # tpyc: warning(/copies list\[Box\] into owned storage/)
        # Mutated after the boundary: `b` still reaches the list's element.
        b.n += 10
        print("overload method", b.n, len(kept))


class Shelf:
    @overload
    def get(self, bs: list[Box]) -> Box: ...
    @overload
    def get(self, bs: list[Box], i: int32) -> Box: ...
    def get(self, bs: list[Box], i: int32 = 0) -> Box:
        return bs[i]


def tuple_static(s: str) -> None:
    # temp: a callee defined below takes a tuple temporary and returns static
    # storage, so the result borrows nothing.
    v: StrView = head_static((s, 1))  # tpyc: ok
    print("tuple static", v)


def head_static(p: tuple[str, int32]) -> StrView:
    return "static"


class CrossMid:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell(1)


class CrossOuter:
    m: CrossMid
    q: Chooser

    def __init__(self) -> None:
        self.m = CrossMid()
        self.q = Chooser()

    # cross-module: the other module's readonly `pick` hands back `f.cell`,
    # written through, so this method writes `self`.
    def write(self) -> None:
        f = self.m
        self.q.pick(f.cell).bump()  # tpyc: ok


class Ticker(Iterator[int32]):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __next__(self) -> int32:
        if self.n > 10:
            raise StopIteration()
        self.n += 1
        return self.n


def same_ticker(t: Ticker) -> Ticker:
    return t.__iter__()


def keep_tickers(ts: Own[list[Ticker]]) -> Own[list[Ticker]]:
    return ts


def synth_iter() -> None:
    # synthesized __iter__: the call through it lends its receiver, so the
    # list a live reference points into is copied, not moved.
    ts = [Ticker(1), Ticker(2)]
    t = same_ticker(ts[0])
    kept = keep_tickers(ts)  # tpyc: warning(/copies list\[Ticker\] into owned storage/)
    t.n += 10
    print("synth iter", t.n, kept[1].n)


def main() -> None:
    o = Outer()
    o.lend_write()
    print("lend write", o.h.inner.n)
    print("lend bind", o.lend_bind())
    o.lend_multi(True)
    o.lend_multi(False)
    # Mutated after the boundary: the shared objects show both writes.
    print("lend multi", o.h.inner.n, o.h.other.n)
    o2 = Outer()
    print("lend gen", list(o2.lend_gen()), o2.h.inner.n)
    print("lend async", asyncio.run(o2.lend_async()), o2.h.inner.n)
    h = Holder()
    print("lend ctor", Built(h).n, h.inner.n)
    lend_free(h)
    print("lend free", h.inner.n)
    lend_closure(h)
    print("lend closure", h.inner.n)
    print("field meth", H().field_meth())
    print("field free", field_free(H()))
    print("field closure", field_closure(H()))
    print("field ctor", FieldCtor(H()).n)
    select_sections(False)
    print("chain", chain(Holder(), False))
    print("walrus", list(walrus_rows([[1, 2]])))
    print("callable", callable_view(view_of, False))
    temp_nested("abc")
    overload_move()
    shelf_user = ShelfUser()
    shelf = Shelf()
    shelf_user.run(shelf)
    tuple_static("abc")
    co = CrossOuter()
    co.write()
    co.write()
    # Mutated after the boundary: both writes reached the shared object.
    print("cross method", co.m.cell.n)
    synth_iter()


main()

# module level: a call result kept across a loop that rebinds the holder it
# points into, while the callee's body is not analyzed yet.
mod_h = Holder()
mod_o = opt_inner(mod_h)
for mod_i in range(2):
    if mod_o is not None:
        print("module", mod_o.n)
    mod_h = Holder()  # tpyc: warning(/'mod_o' will not keep the object it was given/)
    if mod_i == 0:
        mod_o = opt_inner(mod_h)
        # Mutated after the boundary: the next pass reads it through mod_o.
        mod_h.inner.bump()

# module level, cross-module: the same shape over the other module's function,
# whose body is analyzed before this module's statements: its recorded fact
# lends the crate.
x_crate = Crate(1)
x_cell = opt_cell(x_crate)
for x_i in range(2):
    if x_cell is not None:
        print("cross module", x_cell.n)
    x_crate = Crate(10 + x_i)  # tpyc: warning(/'x_cell' will not keep the object it was given/)
    if x_i == 0:
        x_cell = opt_cell(x_crate)
        # Mutated after the boundary: the next pass reads it through x_cell.
        x_crate.cell.bump()
