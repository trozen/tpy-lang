# The rebinding walrus over tuple locals and as a tuple element: borrow and
# per-element-Own locals, tuple returns, whole-owned results, an append.
from tpy import Own, int32

from fixtures import A, H, N, make_pair


def borrow_tuple(h: H) -> None:
    t = (h.a, 1)
    # A borrow tuple local rebound to a list element.
    print("borrow_tuple", (t := h.ps[0])[1])  # tpyc: ok
    t[0].n += 10
    print("borrow_tuple", h.a.n, h.ps[0][0].n)


def own_tuple(h: H) -> int32:
    first = (t := make_pair(9))[0]  # tpyc: ok
    # A per-element-Own tuple local rebound to a field.
    if (t := h.pair)[0] > 0:  # tpyc: ok
        t[1].n += 10
    print("own_tuple", first, h.pair[1].n)
    return t[0]


def tuple_return() -> tuple[Own[A], int32]:
    b = A(1)
    print("tuple_return", b.n)
    # A tuple element moves out as the scalar does.
    return ((b := A(2)), 1)  # tpyc: ok


def tuple_finally_return() -> tuple[Own[N], int32]:
    b = N(1)
    try:
        # A tuple member under a mutating `finally`, non-copyable.
        return ((b := N(2)), 7)  # tpyc: ok
    finally:
        b.n = 99


def whole_tuple_return() -> Own[tuple[A, int32]]:
    b = A(1)
    print("whole_tuple_return", b.n)
    # A whole-owned tuple result moves its walrus element in.
    return ((b := A(2)), 7)  # tpyc: ok


def whole_tuple_finally() -> Own[tuple[A, int32]]:
    b = A(1)
    try:
        # A whole-owned tuple member under a mutating `finally`.
        return ((b := A(2)), 7)  # tpyc: ok
    finally:
        b.n = 99


def tuple_append() -> None:
    b = A(1)
    l: list[tuple[A, int32]] = []
    print("tuple_append", b.n)
    # A tuple element at an owning argument moves at the name's last use.
    l.append(((b := A(2)), 1))  # tpyc: ok
    l[0][0].n += 10
    print("tuple_append", l[0][0].n)


def main() -> None:
    borrow_tuple(H())
    k = own_tuple(H())
    print("own_tuple", k)
    ta = tuple_return()
    ta[0].n += 10
    print("tuple_return", ta[0].n, ta[1])
    tb, tk = tuple_finally_return()
    print("tuple_finally_return", tb.n, tk)
    wt = whole_tuple_return()
    wt[0].n += 10
    print("whole_tuple_return", wt[0].n, wt[1])
    wf = whole_tuple_finally()
    wf[0].n += 1
    print("whole_tuple_finally", wf[0].n, wf[1])
    tuple_append()
