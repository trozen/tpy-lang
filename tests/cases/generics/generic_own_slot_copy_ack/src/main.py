# The silencing half of the generic owning-slot copy warning: copy() spelled at
# the slot acknowledges the duplication, and a moved Own[T] never copied at all.
# Both agree with CPython, so the sections stay parity-checked.
from __future__ import annotations
from tpy import Fn, int32, Own, ValueType, copy


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
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


# inverse: a `T: ValueType` bound makes a reference-type copy impossible, so
# the declaration-time hedge has nothing to say -- the same exemption at the
# RETURN slot that the insert slot applies. It proves the SLOT copies rather
# than aliases, not that the copy shares nothing: a value type carrying a
# `Ptr` field still hands both copies the same pointee
# (BUGS.md#value-type-ptr-field-aliases-through-copy-exemption).
def dup_bounded[T: ValueType](h: VHolder[T]) -> Own[T]:
    return h.borrow()  # tpyc: ok


# the bounded twin: a concrete value payload, silent for the same reason.
class IntHolder:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def borrow(self) -> int32:
        return self.val


def dup_bounded_twin(h: IntHolder) -> Own[int32]:
    return h.borrow()  # tpyc: ok


def sec_value_bound() -> None:
    b = VHolder[int32](7)
    t = IntHolder(7)
    print("value-bound:", dup_bounded(b), dup_bounded_twin(t))


# inverse: a call through a callable VALUE declared to return an open K
# returns what K says at each instantiation -- a borrow at `K = P`, a fresh
# value at `K = Own[P]` -- so the generic body hedges the copy it makes at the
# lending instantiation, while the twin below, which spells the fresh value
# `Own[Cell]`, stays silent. The call passes a fresh-value lambda (K = Own[Cell]).
def apply_generic[T, K](xs: list[T], f: Fn[[T], K], out: list[K]) -> None:
    for x in xs:
        # copies at a K a callable lends (`K = P`), moves at `K = Own[P]`
        out.append(f(x))  # tpyc: warning(/may copy K into owned storage/)


def apply_twin(xs: list[int32], f: Fn[[int32], Own[Cell]], out: list[Cell]) -> None:
    for x in xs:
        out.append(f(x))  # tpyc: ok


def ret_generic[T, K](x: T, f: Fn[[T], K]) -> Own[K]:
    # copies at a K a callable lends (`K = P`) into the owned result
    return f(x)  # tpyc: warning(/may copy K into owned storage/)


def ret_twin(x: int32, f: Fn[[int32], Own[Cell]]) -> Own[Cell]:
    return f(x)  # tpyc: ok


def sec_callable_value() -> None:
    xs: list[int32] = [1]
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
