# Test generic ValueType record: bound checking and template specialization.
from tpy import int32, ValueType


class Vec2(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Pair[T: ValueType](ValueType):
    first: T
    second: T

    def __init__(self, first: T, second: T) -> None:
        self.first = first
        self.second = second


def swap(p: Pair[int32]) -> Pair[int32]:
    return Pair[int32](p.second, p.first)


def main() -> None:
    # Builtin value type as bound
    p = Pair[int32](1, 2)
    q = p                      # copy (value type)
    print(p.first)
    print(q.first)

    s = swap(p)
    print(s.first)
    print(s.second)

    # User ValueType record as bound
    vp = Pair[Vec2](Vec2(1, 2), Vec2(3, 4))
    print(vp.first.x)

    # bool as bound
    bp = Pair[bool](True, False)
    print(bp.first)

main()
