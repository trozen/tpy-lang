# `is` between two `Ptr`s to unrelated classes is rejected (stricter by
# design: docs/LANGUAGE_FEATURES.md, Operators / Comparison); both operands
# are nullable, so the diagnostic spells the both-None test.
from tpy import Ptr


class A:
    n: int

    def __init__(self) -> None:
        self.n = 1


class B:
    n: int

    def __init__(self) -> None:
        self.n = 1


def same(p: Ptr[A], q: Ptr[B]) -> bool:
    return p is q  # tpyc: error(/'A' and 'B' are unrelated \(neither is a subclass of the other\); to test that both are None, write 'p is None and q is None'/)


a = A()
b = B()
print(same(a, b))
