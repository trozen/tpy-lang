# Test generic ValueType record: bound checking and template specialization.
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32


class Pair[T: ValueType](ValueType):
    first: T
    second: T


def swap(p: Pair[Int32]) -> Pair[Int32]:
    return Pair[Int32](p.second, p.first)


def main() -> None:
    # Builtin value type as bound
    p = Pair[Int32](1, 2)
    q = p
    q.first = 10
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
