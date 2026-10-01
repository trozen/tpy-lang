# An operator method with a type parameter of its own is refused at its
# definition (LANGUAGE_FEATURES "Special Methods", the "Not supported" rule):
# its C++ operator could not take the operand generically.
from __future__ import annotations
from tpy import int32, int64, AnyFixedInt, Own


class V:
    x: int64

    def __init__(self, x: int64) -> None:
        self.x = x

    def __lshift__[C: AnyFixedInt](self, k: C) -> Own[V]:  # tpyc: error(/operator method '__lshift__' cannot have its own type parameters/)
        return V(self.x << k)


def main() -> None:
    v = V(3) << int32(2)
    print(v.x)


main()
