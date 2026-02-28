# Test cross-module ValueType: imported record preserves value semantics.
from tpy import Int32
from shapes import Vec2


def modify(v: Vec2) -> None:
    v.x = 99


def main() -> None:
    a = Vec2(1, 2)
    b = a
    b.x = 10
    print(a.x)
    print(b.x)

    modify(a)
    print(a.x)

main()
