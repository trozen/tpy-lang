# An implicit dunder call that grows its receiver (`g[k]`, `g[k] = v`, `k in g`,
# `with g:`, `for x in g:`) warns while a borrow into the receiver is held, as
# the named method does. Each borrow is dead before the call, so this runs; the
# warning does not know that (BUGS.md#borrow-warning-not-last-use-aware).
from typing import Iterator
from tpy import auto_readonly, int32, readonly


class Pt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Grid:
    ps: list[Pt]

    def __init__(self) -> None:
        self.ps = [Pt(1), Pt(2)]

    # Every dunder below appends to `ps`, which may reallocate it.
    @readonly(False)
    def __getitem__(self, i: int32) -> Pt:
        while len(self.ps) <= i:
            self.ps.append(Pt(0))
        return self.ps[i]

    def __setitem__(self, i: int32, v: Pt) -> None:
        while len(self.ps) <= i:
            self.ps.append(Pt(0))
        self.ps[i].x = v.x

    @readonly(False)
    def __contains__(self, i: int32) -> bool:
        while len(self.ps) <= i:
            self.ps.append(Pt(0))
        return True

    def __enter__(self) -> int32:
        self.ps.append(Pt(0))
        return len(self.ps)

    def __exit__(self, kind, value, tb) -> None:
        pass

    def __iter__(self) -> "Grid":
        self.ps.append(Pt(0))
        return self

    def __next__(self) -> int32:
        raise StopIteration()


class Closer:
    ps: list[Pt]
    opened: int32

    def __init__(self) -> None:
        self.ps = [Pt(1)]
        self.opened = 0

    @auto_readonly
    def at(self, i: int32) -> Pt:
        return self.ps[i]

    def __enter__(self) -> int32:
        self.opened += 1
        return self.opened

    # Only `__exit__` grows the manager.
    def __exit__(self, kind, value, tb) -> None:
        self.ps.append(Pt(0))


class Cells:
    ps: list[Pt]

    def __init__(self) -> None:
        self.ps = [Pt(1)]

    @auto_readonly
    def at(self, i: int32) -> Pt:
        return self.ps[i]

    def __getitem__(self, i: int32) -> int32:
        if i < len(self.ps):
            return self.ps[i].x
        return 0

    def __setitem__(self, i: int32, v: int32) -> None:
        while len(self.ps) <= i:
            self.ps.append(Pt(0))
        self.ps[i].x = v


class Bag:
    ps: list[Pt]
    n: int32

    def __init__(self) -> None:
        self.ps = [Pt(1)]
        self.n = 0

    @auto_readonly
    def at(self, i: int32) -> Pt:
        return self.ps[i]

    # No `__contains__`: `x in bag` iterates, and `__iter__` grows the bag.
    def __iter__(self) -> "Bag":
        self.ps.append(Pt(0))
        self.n = 0
        return self

    def __next__(self) -> int32:
        self.n += 1
        if self.n > 3:
            raise StopIteration()
        return self.n


class SubGrid(Grid):
    # Every dunder is inherited from Grid.
    def __init__(self) -> None:
        super().__init__()


class SubCloser(Closer):
    # `__exit__` is inherited from Closer.
    def __init__(self) -> None:
        super().__init__()


def getitem() -> None:
    g = Grid()
    a = g[0]
    print("getitem:", a.x)
    b = g[40]  # tpyc: warning(/Mutation of 'g' while borrowed \('__getitem__' may invalidate references\)/)
    print("getitem:", b.x, len(g.ps))


def rebind() -> None:
    # The reassigned alias re-points (a pointer local); both reads may grow g.
    g = Grid()
    a = g[0]
    a = g[1]  # tpyc: warning(/Mutation of 'g' while borrowed \('__getitem__'/)
    print("rebind:", a.x)
    b = g[40]  # tpyc: warning(/Mutation of 'g' while borrowed \('__getitem__'/)
    print("rebind:", b.x, len(g.ps))


def setitem() -> None:
    g = Grid()
    a = g[0]
    print("setitem:", a.x)
    g[40] = Pt(5)  # tpyc: warning(/Mutation of 'g' while borrowed \('__setitem__' may invalidate references\)/)
    print("setitem:", len(g.ps), g.ps[40].x)


def membership() -> None:
    g = Grid()
    a = g[0]
    print("in:", a.x)
    print("in:", 40 in g, len(g.ps))  # tpyc: warning(/Mutation of 'g' while borrowed \('__contains__'/)


def context_manager() -> None:
    g = Grid()
    a = g[0]
    print("with:", a.x)
    with g as n:  # tpyc: warning(/Mutation of 'g' while borrowed \('__enter__'/)
        print("with:", n)


def for_loop() -> None:
    g = Grid()
    a = g[0]
    print("for:", a.x)
    for v in g:  # tpyc: warning(/Mutation of 'g' while borrowed \('__iter__'/)
        print("for: never", v)
    print("for:", len(g.ps))


def exit_grows() -> None:
    g = Closer()
    a = g.at(0)
    print("exit:", a.x)
    with g as n:  # tpyc: warning(/Mutation of 'g' while borrowed \('__exit__'/)
        print("exit:", n)
    print("exit:", len(g.ps))


def aug_setitem() -> None:
    g = Cells()
    a = g.at(0)
    print("aug:", a.x)
    g[40] += 1  # tpyc: warning(/Mutation of 'g' while borrowed \('__setitem__'/)
    print("aug:", len(g.ps), g[40])


def in_by_iteration() -> None:
    g = Bag()
    a = g.at(0)
    print("in iter:", a.x)
    print("in iter:", 2 in g, len(g.ps))  # tpyc: warning(/Mutation of 'g' while borrowed \('__iter__'/)


def close_it(g: Closer) -> None:
    with g as n:
        print("caller with:", n)


def put(g: Cells) -> None:
    g[40] = 3


def caller_with() -> None:
    # The growing `__exit__` makes `close_it` a structural mutation of its param.
    g = Closer()
    a = g.at(0)
    print("caller with:", a.x)
    close_it(g)  # tpyc: warning(/Passing borrowed container 'g' to non-readonly parameter 'g'/)
    print("caller with:", len(g.ps))


def caller_setitem() -> None:
    # So does the growing `__setitem__` for `put`.
    g = Cells()
    a = g.at(0)
    print("caller setitem:", a.x)
    put(g)  # tpyc: warning(/Passing borrowed container 'g' to non-readonly parameter 'g'/)
    print("caller setitem:", len(g.ps), g[40])


def inherited() -> None:
    # Inherited dunders are judged by their own (base-class) facts.
    g = SubGrid()
    a = g[0]
    print("inherited:", a.x)
    b = g[40]  # tpyc: warning(/Mutation of 'g' while borrowed \('__getitem__'/)
    print("inherited:", b.x, len(g.ps))
    h = SubCloser()
    c = h.at(0)
    print("inherited:", c.x)
    with h as n:  # tpyc: warning(/Mutation of 'h' while borrowed \('__exit__'/)
        print("inherited:", n)
    print("inherited:", len(h.ps))


def comprehension() -> None:
    g = Grid()
    a = g[0]
    print("comprehension:", a.x)
    xs = [g[i].x for i in range(40, 42)]  # tpyc: warning(/Mutation of 'g' while borrowed \('__getitem__'/)
    print("comprehension:", xs, len(g.ps))


def generator() -> Iterator[int32]:
    g = Grid()
    a = g[0]
    yield a.x
    yield g[40].x  # tpyc: warning(/Mutation of 'g' while borrowed \('__getitem__'/)
    yield len(g.ps)


def main() -> None:
    getitem()
    rebind()
    setitem()
    membership()
    context_manager()
    for_loop()
    exit_grows()
    aug_setitem()
    in_by_iteration()
    caller_with()
    caller_setitem()
    inherited()
    comprehension()
    for v in generator():
        print("generator:", v)


main()
