# Where an owned step's source is a parameter: a local bound from an
# `Iterable[Own[Cell]]` parameter forwards it, under an alias too and
# rebound from its alias, and the caller's list is copied, not consumed;
# an owned step of a `T: ValueType` parameter binds as its concrete twin
# does.
from typing import Iterable
from tpy import int32, Own, ValueType, copy_iter
from fixtures import Cell, take


def via_local(p: Iterable[Own[Cell]]) -> int32:
    it = p
    return len(take(it))  # tpyc: ok


def via_alias(p: Iterable[Own[Cell]]) -> int32:
    it = p
    jt = it
    return len(take(jt))  # tpyc: ok


def via_rebind(p: Iterable[Own[Cell]]) -> int32:
    it = p
    jt = it
    # the rebind passes the same object on; `it = p` itself copies the
    # parameter into the local (BUGS.md#rebound-local-copies-iterable-param,
    # BUGS.md#iterator-rebind-copies-cursor)
    it = jt
    return len(take(it))  # tpyc: ok


def generic_owned[T: ValueType](xs: Iterable[Own[T]]) -> Own[list[T]]:
    out: list[T] = []
    for x in xs:  # an open value step binds as its instantiation does
        out.append(x)
    return out


def concrete_str(xs: Iterable[Own[str]]) -> Own[list[str]]:
    out: list[str] = []
    for x in xs:
        out.append(x)
    return out


def concrete_int(xs: Iterable[Own[int32]]) -> Own[list[int32]]:
    out: list[int32] = []
    for x in xs:
        out.append(x)
    return out


def main() -> None:
    ys = [Cell(1), Cell(2)]
    ys.append(Cell(3))
    print("param local", via_local(ys), len(ys))  # tpyc: warning(/copies Cell elements/)
    print("param alias", via_alias(ys), len(ys))  # tpyc: warning(/copies Cell elements/)
    print("param rebind", via_rebind(ys), len(ys))  # tpyc: warning(/copies Cell elements/)
    ss = ["ab", "c"]
    gs = generic_owned(copy_iter(ss))  # tpyc: ok
    cs = concrete_str(copy_iter(ss))  # tpyc: ok
    ss[0] = "z"
    print("generic str", gs, cs, ss)
    ns = [1, 2]
    gn = generic_owned(copy_iter(ns))  # tpyc: ok
    cn = concrete_int(copy_iter(ns))  # tpyc: ok
    ns[0] = 9
    print("generic int32", gn, cn, ns)
