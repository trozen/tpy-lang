# Held operands and packs under a guard: a wide literal, packs in a held
# operand, in an `assert` condition and in a match guard; frame and
# comprehension-source temporaries in a match guard and an `assert`, fed
# by a class-pattern capture; a mutating callee under a false guard.
from typing import Iterator
from tpy import int32
from fixtures import P, total


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        print("guards-mk A", n)
        self.n = n


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pair_gen(a: A, r: A) -> Iterator[int32]:
    yield a.n
    r.n += 1
    yield r.n


def one(a: A) -> Iterator[int32]:
    yield a.n


def pick_c(cs: list[C], r: A) -> list[C]:
    r.n += 10
    return cs


def bump(p: P) -> P:
    p.n += 1
    return p


def pick(p: P) -> P:
    return p


def conditions(q: int32, base: int32) -> None:
    # A nonzero `base` makes each guard false where its pattern matches.
    r = A(base)
    # A frame-factory call with a temporary argument in a match guard: the
    # frame writes the caller's `r`, read after the guard.
    match q:
        case 3 if sum(pair_gen(A(3), r)) == 4:  # tpyc: ok
            print("guards-mg-frame", r.n)
        case _:
            print("guards-mg-frame-other", r.n)
    # A comprehension source needing an argument temporary in a match guard
    # and in an `assert`; the lending callee writes the caller's `r`.
    match q:
        case 3 if len([c.n for c in pick_c([C(8)], r)]) == 1 + base:  # tpyc: ok
            print("guards-mg-comp", r.n)
        case _:
            print("guards-mg-comp-other", r.n)
    assert len([c.n for c in pick_c([C(1)], r)]) == 1  # tpyc: ok
    print("guards-assert-comp", r.n)


def captured(p: P) -> None:
    # A class-pattern capture feeding the guard's frame temporary.
    match p:
        case P(n=v) if sum(one(A(v))) > 0:  # tpyc: ok
            print("guards-capture", v)
        case _:
            print("guards-capture-other", p.n)


def captured_gen(p: P) -> Iterator[int32]:
    # The same guard in a generator body.
    match p:
        case P(n=v) if sum(one(A(v))) > 0:  # tpyc: ok
            yield v
        case _:
            yield -1


def false_guard(flag: bool) -> None:
    p2 = P(3)
    # A mutating callee under a false guard does not run.
    k2 = (bump(pick(p2)).n or 9) if flag else 0  # tpyc: ok
    print("guards-seated-lvalue", k2, p2.n)


def wide(flag: bool) -> None:
    # The wide literal is held (banked under the guard). The warning is
    # right: the literal is an `int` in the program.
    wide_r = flag and (9000000000000000000 and 2) > 0  # tpyc: warning(/outside default int32 range/)
    print("guards-wide", wide_r)


def literal_split(flag: bool, v: int32) -> None:
    # `2` is a literal, read in place; `-1` is a unary minus, not a
    # literal, so it is held (banked under the guard).
    pos_r = flag and (2 and v) > 0  # tpyc: ok
    neg_r = flag and (-1 and v) > 0  # tpyc: ok
    print("guards-literal-split", pos_r, neg_r)


def packs(q: int32, name: str) -> int32:
    # A pack in the held operand of an `if` condition and of a guarded
    # select, in a ternary arm.
    if (total(name, name + "-if") or 5) == 7:  # tpyc: ok
        print("guards-if-pack")
    arm_r = q > 0 and ((1 if q > 5 else total(name, name + "-arm")) or 5) > 0  # tpyc: ok
    print("guards-arm-pack", arm_r)
    # A pack in an `assert` condition and in a match guard.
    assert total(name, name + "-assert") > 0  # tpyc: ok
    match q:
        case 1 if total(name, name + "-case") == 9:  # tpyc: ok
            print("guards-match-pack")
        case _:
            print("guards-match-other")
    return 10 if q == 1 else 20


def main() -> None:
    wide(True)
    wide(False)
    p1 = packs(1, "ab")
    p7 = packs(7, "ab")
    print("guards-packs", p1, p7)
    conditions(3, 0)
    conditions(3, 5)
    conditions(4, 0)
    captured(P(2))
    captured(P(0))
    for g in captured_gen(P(4)):
        print("guards-capture-gen", g)
    for g in captured_gen(P(0)):
        print("guards-capture-gen", g)
    literal_split(True, 3)
    literal_split(False, 3)
    false_guard(False)
    false_guard(True)
