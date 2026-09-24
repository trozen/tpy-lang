# The quiet side of a tuple element's Own[T] slot: a fresh member, an explicit
# copy() and an owned local at its last use move or copy with no warning. The
# container-element sink has the same quiet side for a tuple local.
from typing import Self
import tpy
from tpy import int32, Own, copy


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]


class Holder:
    a: P

    def __init__(self) -> None:
        self.a = P(3)

    # a consuming method moves its own field once
    def give(self: Own[Self]) -> int32:
        return take((self.a, P(4)))  # tpyc: ok


def take(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    a.xs.append(100)
    return len(a.xs) * 10 + len(b.xs)


def ret_moves() -> tuple[Own[P], int32]:
    n = P(5)
    # an owned local at its last use moves into the returned element
    return (n, 1)  # tpyc: ok


def fresh_into_list() -> Own[list[tuple[P, P]]]:
    t = (P(1), P(2))
    # a local that owns every element aliases nothing: the store is quiet
    return [t]  # tpyc: ok


def fresh_into_dict() -> Own[dict[int32, tuple[P, P]]]:
    t = (P(1), P(2))
    # the same at a dict value
    return {1: t}  # tpyc: ok


def copy_into_list(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # copy() declares the copy of a local's borrowed elements
    return [copy(t)]  # tpyc: ok


def copy_module_into_list(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # the module-qualified spelling is the same copy()
    return [tpy.copy(t)]  # tpyc: ok


def copy_into_dict(p: P) -> Own[dict[int32, tuple[P, int32]]]:
    t = (p, 3)
    # the same at a dict value
    return {1: copy(t)}  # tpyc: ok


def copy_into_comp(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # the same in a comprehension element, one copy per item
    return [copy(t) for _ in range(2)]  # tpyc: ok


def copy_into_append(p: P, xs: list[tuple[P, P]]) -> None:
    t = (p, p)
    # the same at an append argument, and at t's last use it is no
    # "unnecessary copy()": the borrowed elements would be copied anyway
    xs.append(copy(t))  # tpyc: ok


def copy_return(p: P) -> Own[tuple[P, P]]:
    t = (p, p)
    # the same at a returned value
    return copy(t)  # tpyc: ok


def copy_nested_member(p: P) -> Own[list[tuple[tuple[P, P], int32]]]:
    t = (p, p)
    # the same as a tuple member inside the element (its read-back is
    # BUGS.md#nested-storage-tuple-element-read)
    return [(copy(t), 1)]  # tpyc: ok


def copy_param(t: tuple[P, P]) -> Own[list[tuple[P, P]]]:
    # the same for a tuple PARAMETER
    return [copy(t)]  # tpyc: ok


def values_into_list(n: int32) -> Own[list[tuple[int32, int32]]]:
    t = (n, 2)
    # a value-only tuple has nothing to copy
    return [t]  # tpyc: ok


def main() -> None:
    c = P(7)
    d = P(8)
    # a fresh member and an explicit copy() take no warning
    print("fresh_copy", take((P(7), copy(c))), len(c.xs))  # tpyc: ok
    # owned locals at their last use move in
    print("last_use", take((c, d)))  # tpyc: ok
    print("give", Holder().give())
    m, k = ret_moves()
    m.xs.append(6)
    print("ret_moves", len(m.xs), k)
    for f0, f1 in fresh_into_list():
        f0.xs.append(1)
        print("fresh_into_list", len(f0.xs), len(f1.xs))
    fd = fresh_into_dict()
    fa, fb = fd[1]
    print("fresh_into_dict", len(fa.xs), len(fb.xs))
    q = P(1)
    cl = copy_into_list(q)
    cm = copy_module_into_list(q)
    cd = copy_into_dict(q)
    cc = copy_into_comp(q)
    ca: list[tuple[P, P]] = []
    copy_into_append(q, ca)
    cr = copy_return(q)
    cn2 = copy_nested_member(q)
    cpar = copy_param((q, q))
    # the copies do not see a later mutation of the original
    q.xs.append(5)
    for c0, c1 in cl:
        print("copy_into_list", len(c0.xs), len(c1.xs))
    for c0, c1 in cm:
        print("copy_module_into_list", len(c0.xs), len(c1.xs))
    for c0, c1 in cc:
        print("copy_into_comp", len(c0.xs), len(c1.xs))
    cp, cn = cd[1]
    print("copy_into_dict", len(cp.xs), cn)
    for c0, c1 in ca:
        print("copy_into_append", len(c0.xs), len(c1.xs))
    r0, r1 = cr
    print("copy_return", len(r0.xs), len(r1.xs))
    print("copy_nested_member", len(cn2))
    for c0, c1 in cpar:
        print("copy_param", len(c0.xs), len(c1.xs))
    print("values_into_list", values_into_list(1))


main()
