# The silencing half of the generic owning-slot copy warning: copy() spelled at
# the slot acknowledges the duplication, and a moved Own[T] never copied at all.
# Both agree with CPython, so the sections stay parity-checked.
from __future__ import annotations
from tpy import Fn, Int32, Own, ValueType, copy


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


# copy() spelled at the owning slot: the duplication is intended, no warning.
def collect_ack[T](h: GHolder[T], xs: list[T]) -> None:
    xs.append(copy(h.borrow()))  # tpyc: ok


# inverse: an Own[T] moved through a generic body is not a copy at all.
def relay_owned[T](v: Own[T], xs: list[T]) -> None:
    xs.append(v)  # tpyc: ok


class VHolder[T: ValueType]:
    val: T

    def __init__(self, v: Own[T]) -> None:
        self.val = v

    def borrow(self) -> T:
        return self.val


# inverse: a `T: ValueType` bound settles the verdict the hedge would defer to
# the instantiation, so the body stays silent -- the same exemption at the
# RETURN slot that the insert slot applies. It proves the SLOT copies rather
# than aliases, not that the copy shares nothing: a value type carrying a
# `Ptr` field still hands both copies the same pointee
# (BUGS.md#value-type-ptr-field-aliases-through-copy-exemption).
def dup_bounded[T: ValueType](h: VHolder[T]) -> Own[T]:
    return h.borrow()  # tpyc: ok


# the bounded twin: a concrete value payload, silent for the same reason.
class IntHolder:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def borrow(self) -> Int32:
        return self.val


def dup_bounded_twin(h: IntHolder) -> Own[Int32]:
    return h.borrow()  # tpyc: ok


def sec_value_bound() -> None:
    b = VHolder[Int32](7)
    t = IntHolder(7)
    print("value-bound:", dup_bounded(b), dup_bounded_twin(t))


# inverse: a call through a callable VALUE is an rvalue whatever K resolves
# to -- the Fn type is not a declaration whose return convention was checked,
# and the body that runs builds a fresh value. The generic must stay as silent
# as the monomorphic twin below it. This pins ONLY the absence of a false
# positive for a FRESH-value callable; a callable bound to a borrow-returning
# function copies here with no diagnostic either, which is a real defect
# (BUGS.md#callable-value-borrow-return-copies-unwarned), not what this
# section asserts.
def apply_generic[T, K](xs: list[T], f: Fn[[T], K], out: list[K]) -> None:
    for x in xs:
        out.append(f(x))  # tpyc: ok


def apply_twin(xs: list[Int32], f: Fn[[Int32], Cell], out: list[Cell]) -> None:
    for x in xs:
        out.append(f(x))  # tpyc: ok


def ret_generic[T, K](x: T, f: Fn[[T], K]) -> Own[K]:
    return f(x)  # tpyc: ok


def ret_twin(x: Int32, f: Fn[[Int32], Cell]) -> Own[Cell]:
    return f(x)  # tpyc: ok


def sec_callable_value() -> None:
    xs: list[Int32] = [1]
    g: list[Cell] = []
    apply_generic(xs, lambda v: Cell(v), g)
    t: list[Cell] = []
    apply_twin(xs, lambda v: Cell(v), t)
    print("callable-value:", g[0].n, t[0].n,
          ret_generic(3, lambda v: Cell(v)).n, ret_twin(3, lambda v: Cell(v)).n)


def sec_ack() -> None:
    g = GHolder[Cell](Cell(1))
    xs: list[Cell] = []
    collect_ack(g, xs)
    g.borrow().n = 99
    print("ack:", g.borrow().n, xs[0].n)


def sec_moved() -> None:
    xs: list[Cell] = []
    relay_owned(Cell(1), xs)
    xs[0].n = 5
    print("moved:", xs[0].n)


def main() -> None:
    sec_ack()
    sec_moved()
    sec_callable_value()
    sec_value_bound()


main()
