# A RECORD-valued property as a method receiver: composing the method over the
# borrow-returning getter has no arm, so `c.p.bump()` is rejected. The
# container-valued property, which does compose, is pinned by
# tests/cases/records/property_ref_semantics.
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 1


class C:
    _p: P

    def __init__(self) -> None:
        self._p = P(1)

    @property
    def p(self) -> P:
        return self._p


def use_record(c: C) -> None:
    c.p.bump()  # tpyc: error(/method/)


def main() -> None:
    c = C()
    use_record(c)
    print(c.p.x)


main()
