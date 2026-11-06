# The rebinding walrus over value locals (scalars, value Optionals, str,
# slot-hinted values) and the `nonlocal` rebind to None.
from typing import Callable

from tpy import int32

from fixtures import A, take


def value_locals() -> None:
    i = 1
    # A value local keeps the plain assignment.
    print("value", (i := i + 1))  # tpyc: ok
    print("value", i)
    m: int32 | None = None
    if (m := 4) is not None:  # tpyc: ok
        print("value", m + 1)


def value_opt_return() -> int32:
    m: int32 | None = None
    # The walrus's type is the read of `m` after the write, as `m = 4;
    # return m` has.
    r = (m := 3)  # tpyc: type(int32)
    return (m := r + 1)  # tpyc: ok


def str_append() -> None:
    s = "ab"
    t = s
    # The self-append writes in place, as `s = s + "!"` does.
    print("str", (s := s + "!"))  # tpyc: ok
    print("str", t, s)


def read_elsewhere() -> None:
    s = "ab"
    # The statement reads `s` again, so the append is not done in place. A
    # ternary, not `(s := s + "!") + s`, whose operands C++ evaluates
    # right to left (BUGS.md#subexpression-right-to-left-eval).
    u = (s := s + "!") if len(s) > 0 else s  # tpyc: ok
    print("read_elsewhere", u, s)


def live_self() -> None:
    x = "abc"
    take(x)
    # `x` stays live through the walrus's own value.
    print("live_self", (x := x + "!"))  # tpyc: ok


def slot_hint() -> None:
    g: Callable[[int32], int32] = lambda a: a + 1
    # The lambda's parameter types come from the slot, as for `g = ...`.
    if (g := lambda a: a * 3)(1) > 0:  # tpyc: ok
        print("slot_hint", g(3))
    ys: list[int32] = [1]
    zs = ys
    # An empty literal takes the local's element type.
    print("slot_hint", len((ys := [])))  # tpyc: ok
    ys.append(5)
    print("slot_hint", zs, ys)


def nonlocal_none() -> None:
    p: A | None = A(1)
    keep = p

    def reset() -> None:
        nonlocal p
        # The one rebind of a `nonlocal` reference local: to None, which
        # leaves the old object to its alias.
        print("nonlocal_none", (p := None) is None)  # tpyc: ok
    reset()
    print("nonlocal_none", p is None, keep.n if keep is not None else -1)


def main() -> None:
    value_locals()
    m = value_opt_return()
    print("value", m)
    str_append()
    read_elsewhere()
    live_self()
    slot_hint()
    nonlocal_none()
