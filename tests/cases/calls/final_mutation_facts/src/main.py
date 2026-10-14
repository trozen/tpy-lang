# A call site's copy of its callee's signature (a constructor, a generic
# instantiation) reads the callee's FINAL mutation facts, wherever it is defined.
from typing import Iterator, Self

from tpy import Own, StrView, auto_readonly, pure, readonly
from tpy.extern import cpp_template

from imported import Item, Shelf, Stacker


class Inner:
    def __init__(self, v: int) -> None:
        self.v = v


def mutate_early(i: Inner) -> None:
    i.v += 1


class HolderEarly:
    n: int

    def __init__(self, inner: Inner) -> None:
        mutate_early(inner)
        self.n = inner.v


class HolderLate:
    n: int

    def __init__(self, inner: Inner) -> None:
        mutate_late(inner)
        self.n = inner.v


def mutate_late(i: Inner) -> None:
    i.v += 1


class Maker:
    def make(self) -> int:
        # ctor_direct_order: constructs a class whose body is analyzed later.
        h = HolderDirect(Inner(5))  # tpyc: ok
        return h.n


class HolderDirect:
    n: int

    def __init__(self, inner: Inner) -> None:
        inner.v += 1
        self.n = inner.v


# ctor_global: a module-level initializer binds the same named temporary.
G = HolderDirect(Inner(7))  # tpyc: ok


class P:
    def __init__(self, n: int) -> None:
        self.n = n


def grow(xs: list[P]) -> None:
    xs.append(P(9))


def bump_all(ps: list[P]) -> None:
    for p in ps:
        p.n += 1


class Grower:
    def __init__(self, xs: list[P]) -> None:
        grow(xs)


class FieldWriter:
    def __init__(self, xs: list[P]) -> None:
        bump_all(xs)


def drop_last[T](xs: list[T]) -> None:
    xs.pop()


def via[T](xs: list[T]) -> None:
    drop_last(xs)


class Relay[T]:
    k: int

    def __init__(self) -> None:
        self.k = 0

    def via(self, xs: list[T]) -> None:
        drop_last(xs)


@cpp_template("static_cast<void>({0}.size())")
@readonly
def peek(xs: list[P]) -> None: ...


@cpp_template("static_cast<void>({0}.size())")
@pure
def weigh(xs: list[P]) -> None: ...


def forward_put(xs: list[Item]) -> None:
    s = Stacker()
    head = xs[0]
    # overload_stub_write: the stub carries its implementation's write.
    s.put(xs)  # tpyc: warning(/Passing borrowed container/)


class ViewRunner:
    k: int

    def __init__(self) -> None:
        self.k = 0

    def run(self, v: readonly["Viewer"], ys: list[int]) -> None:
        # auto_readonly_clone_write: the const clone writes a non-self param.
        v.view(ys)  # tpyc: ok


class Viewer:
    data: list[int]

    def __init__(self) -> None:
        self.data = [1]

    def view(self: auto_readonly[Self], ys: list[int]) -> auto_readonly[list[int]]:
        ys.append(7)
        return self.data


def keep(xs: Own[list[P]]) -> Own[list[P]]:
    return xs


def ctor_gen() -> Iterator[int]:
    # ctor_generator: a constructor rvalue argument its __init__ writes.
    h = HolderDirect(Inner(3))  # tpyc: ok
    yield h.n


def ctor_closure() -> int:
    def make() -> int:
        # ctor_closure: a constructor rvalue argument its __init__ writes.
        h = HolderLate(Inner(4))  # tpyc: ok
        return h.n
    return make()


def ctor_sections() -> None:
    # ctor_after: the helper that writes the argument is defined after the class.
    b = HolderLate(Inner(2))  # tpyc: ok
    print("ctor_after", b.n)
    i = Inner(10)
    h = HolderLate(i)
    i.v += 100
    print("ctor_after", h.n, i.v)

    # ctor_before: the helper is defined before the class.
    e = HolderEarly(Inner(2))  # tpyc: ok
    print("ctor_before", e.n)
    j = Inner(20)
    f = HolderEarly(j)
    j.v += 100
    print("ctor_before", f.n, j.v)

    print("ctor_direct_order", Maker().make())
    print("ctor_global", G.n)


def borrow_sections() -> None:
    # ctor_borrow: the constructor grows its argument through a helper.
    xs = [P(1)]
    first = xs[0]
    _ = Grower(xs)  # tpyc: warning(/Passing borrowed container/)
    print("ctor_borrow", len(xs))

    # generic_borrow: generic callees grow it through a generic helper.
    ys = [P(1), P(2), P(3)]
    y0 = ys[0]
    via(ys)  # tpyc: warning(/Passing borrowed container/)
    print("generic_borrow", len(ys))
    zs = [P(1), P(2), P(3)]
    z0 = zs[0]
    relay: Relay[P] = Relay()
    relay.via(zs)  # tpyc: warning(/Passing borrowed container/)
    print("generic_borrow", len(zs))

    # imported_readonly_borrow: an imported method inferred readonly grows it.
    shelf = Shelf()
    items = [Item(1)]
    head = items[0]
    shelf.put(items)  # tpyc: warning(/Passing borrowed container/)
    print("imported_readonly_borrow", len(items))

    # imported_readonly_loop_write: the loop variable binds mutable for an
    # imported method inferred readonly that writes its argument.
    bumped = [Item(1), Item(2)]
    for it in bumped:
        shelf.bump(it)  # tpyc: ok
    print("imported_readonly_loop_write", bumped[0].n, bumped[1].n)


def silent_sections() -> None:
    # field_only_silent: callees that write only the elements' fields.
    xs = [P(1)]
    first = xs[0]
    _ = FieldWriter(xs)  # tpyc: ok
    first.n += 10
    print("field_only_silent", first.n, xs[0].n)
    shelf = Shelf()
    items = [Item(1)]
    head = items[0]
    shelf.touch_all(items)  # tpyc: ok
    head.n += 10
    print("field_only_silent", head.n, items[0].n)

    # bodyless_readonly_silent: a bodyless callee declared readonly.
    ps = [P(5)]
    p0 = ps[0]
    peek(ps)  # tpyc: ok
    p0.n += 1
    print("bodyless_readonly_silent", p0.n, ps[0].n)

    # bodyless_pure_silent: a bodyless callee declared pure.
    qs = [P(5)]
    q0 = qs[0]
    weigh(qs)  # tpyc: ok
    q0.n += 1
    print("bodyless_pure_silent", q0.n, qs[0].n)


def later_sections() -> None:
    for n in ctor_gen():
        print("ctor_generator", n)
    print("ctor_closure", ctor_closure())
    # ctor_comprehension: one named temporary per iteration.
    print("ctor_comprehension", [HolderLate(Inner(k)).n for k in [1, 2]])  # tpyc: ok

    items = [Item(1)]
    forward_put(items)
    print("overload_stub_write", len(items))

    ys: list[int] = []
    runner = ViewRunner()
    viewer = Viewer()
    runner.run(viewer, ys)
    print("auto_readonly_clone_write", len(ys))

    # forward_generic_borrow: a generic callee defined later borrows `xs`, so
    # the consume below copies rather than moving out from under `r`.
    xs = [P(1), P(2)]
    r = first_late(xs)
    kept = keep(xs)  # tpyc: warning(/copies list\[P\] into owned storage/)
    r.n = 5
    print("forward_generic_borrow", r.n, xs[0].n, len(kept))

    # pending_generic_receiver: `Bag()` infers its type argument later, so the
    # call reads a view of `tag`; the consume copies rather than moving `b`.
    b = Bag()
    t: StrView = b.tag()
    k = Keeper(b)  # tpyc: warning(/copies Bag\[int\] into owned storage/)
    print("pending_generic_receiver", t, k.bag.name)


def first_late[T](xs: list[T]) -> T:
    return xs[0]


class Bag[T]:
    name: str

    def __init__(self) -> None:
        self.name = "a long enough name to live on the heap"

    def tag(self) -> StrView:
        return self.name


class Keeper:
    bag: Bag[int]

    def __init__(self, bag: Own[Bag[int]]) -> None:
        self.bag = bag


ctor_sections()
borrow_sections()
silent_sections()
later_sections()
