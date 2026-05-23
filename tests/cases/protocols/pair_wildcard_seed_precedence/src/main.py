# Wildcard partial-explicit type args on a multi-param generic record
# constructor under an LHS hint. With the bidir-hint design, the LHS-derived
# seed binds `A=Box[Pet]` and `B=Int32`; the wildcard at position A means
# "infer from arg" but the merge leaves seed[A] in place at the wildcard slot.
# That gives the inner `Box(Dog(...))` arg a `Box[Pet]` hint, Box's
# record-construction LHS-hint preference then flips its T from Dog to Pet,
# and the assignment resolves cleanly.
#
# Without the seed filling the wildcard slot, the inner `Box(Dog())` would
# analyze without a hint -> T=Dog, the outer `Pair` would infer A=Box[Dog],
# and the assignment to `Pair[Box[Pet], Int32]` would fail with a type
# mismatch. So the wildcard-vs-seed precedence is observable here, unlike
# the single-param case where wildcard and bare are indistinguishable.
from typing import Protocol
from tpy import dynamic, Own, Int32
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Pair[A, B]:
    a: A
    b: B
    def __init__(self, a: Own[A], b: Own[B]) -> None:
        self.a = a
        self.b = b


def main() -> None:
    p: Pair[Box[Pet], Int32] = Pair[_, Int32](Box(Dog("Rex")), 5)  # tpyc: type(Pair[Box[Pet], Int32])
    print(p.a.get().name())
    print(p.b)


main()
