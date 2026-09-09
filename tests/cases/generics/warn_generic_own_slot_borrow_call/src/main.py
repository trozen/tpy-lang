# A generic body filling an owning slot from a BORROW-RETURNING call copies the
# payload exactly as the monomorphic twin does, so it carries the same warning.
# no_cpython: each section mutates the source after the boundary to show the
# copy, which is the acknowledged TPy-copies/CPython-aliases divergence.
from __future__ import annotations
from tpy import Int32, Own, auto_readonly, readonly


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class GHolder[T]:
    val: T

    def __init__(self, v: Own[T]) -> None:
        self.val = v

    def borrow(self) -> T:
        return self.val


class Twin:
    val: Cell

    def __init__(self, v: Own[Cell]) -> None:
        self.val = v

    def borrow(self) -> Cell:
        return self.val


# generic free function: the payload is still an open T at this slot.
def collect_generic[T](h: GHolder[T], xs: list[T]) -> None:
    xs.append(h.borrow())  # tpyc: warning(/copy T into owned storage/)


# the monomorphic twin: the same body with the payload monomorphized.
def collect_twin(h: Twin, xs: list[Cell]) -> None:
    xs.append(h.borrow())  # tpyc: warning(/copies Cell into owned storage/)


# generic method: the same slot inside a generic record's own body.
class GRelay[T]:
    kept: list[T]

    def __init__(self) -> None:
        self.kept = []

    def take(self, h: GHolder[T]) -> None:
        self.kept.append(h.borrow())  # tpyc: warning(/copy T into owned storage/)


# Own[T] return: the same borrow at the return slot, the other owning-slot
# form -- and the identical hedged text, since the verdict is the
# instantiation's there too.
def dup_return[T](h: GHolder[T]) -> Own[T]:
    return h.borrow()  # tpyc: warning(/may copy T into owned storage/)


# the return slot's twin: monomorphized, so the text names the type it copies.
def dup_return_twin(h: Twin) -> Own[Cell]:
    return h.borrow()  # tpyc: warning(/copies Cell into owned storage/)


class GBox[T]:
    val: T

    def __init__(self, v: Own[T]) -> None:
        self.val = v

    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self.val

    # An owning CTOR slot filled from a borrow-returning call -- tplib's
    # Box.clone before it spelled copy(). The argument is hoisted into a temp
    # and moved in, and this section is the only thing in the corpus that
    # reaches that render. No monomorphic twin sits beside it because none
    # compiles: the same body with `Cell` spelled directly is refused at
    # lowering (`method.ret_type`), the second witness of the twin asymmetry
    # in BUGS.md#generic-own-slot-borrow-call-unwarned.
    @readonly
    def dup(self) -> Own[GBox[T]]:
        return GBox(self.get())  # tpyc: warning(/may copy readonly\[T\] into owned storage/)


def sec_own_param() -> None:
    b = GBox[Cell](Cell(1))
    d = b.dup()
    b.get().n = 99
    print("own-param:", b.get().n, d.get().n)


def sec_return() -> None:
    g = GHolder[Cell](Cell(1))
    d = dup_return(g)
    g.borrow().n = 99
    print("return:", g.borrow().n, d.n)
    t = Twin(Cell(1))
    dt = dup_return_twin(t)
    t.borrow().n = 99
    print("return-twin:", t.borrow().n, dt.n)


def sec_generic() -> None:
    g = GHolder[Cell](Cell(1))
    xs: list[Cell] = []
    collect_generic(g, xs)
    g.borrow().n = 99
    print("generic:", g.borrow().n, xs[0].n)


def sec_twin() -> None:
    t = Twin(Cell(1))
    xs: list[Cell] = []
    collect_twin(t, xs)
    t.borrow().n = 99
    print("twin:", t.borrow().n, xs[0].n)


def sec_method() -> None:
    g = GHolder[Cell](Cell(1))
    r = GRelay[Cell]()
    r.take(g)
    g.borrow().n = 99
    print("method:", g.borrow().n, r.kept[0].n)


def main() -> None:
    sec_generic()
    sec_twin()
    sec_method()
    sec_return()
    sec_own_param()


main()
