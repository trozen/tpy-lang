# Cross-module ValueType: an imported value-type record constructs and
# reads correctly across the module boundary.
from shapes import Vec2


def length_sq(v: Vec2) -> int:
    return v.x * v.x + v.y * v.y


def main() -> None:
    a = Vec2(3, 4)
    b = a                      # copy (value type)
    print(a.x)
    print(b.y)
    print(length_sq(a))        # 25


main()
