# Mutually-recursive records (A holds list[B], B holds list[A]) must compile --
# the copyability query must terminate when a field cycles back through the OTHER
# record, not only on a direct self-reference. Reads/mutations go through the
# stored containers so the warned value-into-container copy stays CPython-parity.
from __future__ import annotations
from tpy import Int32


class A:
    val: Int32
    bs: list[B]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.bs = []


class B:
    val: Int32
    as_: list[A]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.as_ = []


def add_b(a: A, b: B) -> None:
    a.bs.append(b)  # tpyc: warning(/copies B into owned storage/)


def add_a(b: B, a: A) -> None:
    b.as_.append(a)  # tpyc: warning(/copies A into owned storage/)


def main() -> None:
    a = A(1)
    b = B(2)
    add_b(a, b)
    add_a(a.bs[0], A(3))
    a.bs[0].val = 200
    a.bs[0].as_[0].val = 30
    print(a.val, a.bs[0].val, a.bs[0].as_[0].val, len(a.bs), len(a.bs[0].as_))


main()
