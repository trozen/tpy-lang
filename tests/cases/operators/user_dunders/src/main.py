# Test user-defined dunder methods on records: binary/unary operators,
# comparisons, __contains__, inplace ops, __hash__, __len__/size()
from __future__ import annotations
from tpy import Int32, UInt64, Own

class Vec2:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: Vec2) -> Own[Vec2]:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Own[Vec2]:
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: Int32) -> Own[Vec2]:
        return Vec2(self.x * scalar, self.y * scalar)

    def __neg__(self) -> Own[Vec2]:
        return Vec2(-self.x, -self.y)

    def __pos__(self) -> Own[Vec2]:
        return Vec2(+self.x, +self.y)

    def __eq__(self, other: Vec2) -> bool:
        return self.x == other.x and self.y == other.y

    def __hash__(self) -> UInt64:
        return UInt64(self.x * 31 + self.y)

    def __len__(self) -> Int32:
        return Int32(2)

    def __contains__(self, value: Int32) -> bool:
        return value == self.x or value == self.y

    def __iadd__(self, other: Vec2) -> Vec2:
        self.x += other.x
        self.y += other.y
        return self

    def __isub__(self, other: Vec2) -> Vec2:
        self.x -= other.x
        self.y -= other.y
        return self

class Score:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def __eq__(self, other: Score) -> bool:
        return self.value == other.value

    def __lt__(self, other: Score) -> bool:
        return self.value < other.value

    def __le__(self, other: Score) -> bool:
        return self.value <= other.value

    def __gt__(self, other: Score) -> bool:
        return self.value > other.value

    def __ge__(self, other: Score) -> bool:
        return self.value >= other.value

class Mask:
    bits: Int32

    def __init__(self, bits: Int32) -> None:
        self.bits = bits

    def __invert__(self) -> Own[Mask]:
        return Mask(~self.bits)

def test_unary() -> None:
    v = Vec2(Int32(3), Int32(4))
    neg = -v
    print(neg.x)
    print(neg.y)
    pos = +v
    print(pos.x)
    print(pos.y)

def test_contains() -> None:
    v = Vec2(Int32(10), Int32(20))
    print(Int32(10) in v)
    print(Int32(20) in v)
    print(Int32(99) in v)
    print(Int32(99) not in v)
    print(Int32(10) not in v)

def test_sub_mul() -> None:
    a = Vec2(Int32(5), Int32(7))
    b = Vec2(Int32(2), Int32(3))
    d = a - b
    print(d.x)
    print(d.y)
    s = a * Int32(3)
    print(s.x)
    print(s.y)

def test_iadd() -> None:
    v = Vec2(Int32(1), Int32(2))
    v += Vec2(Int32(3), Int32(4))
    print(v.x)
    print(v.y)

def test_isub() -> None:
    v = Vec2(Int32(10), Int32(20))
    v -= Vec2(Int32(3), Int32(5))
    print(v.x)
    print(v.y)

def test_hash() -> None:
    v = Vec2(Int32(1), Int32(2))
    h = hash(v)
    print(h > 0)

def test_len() -> None:
    v = Vec2(Int32(1), Int32(2))
    print(len(v))

def test_eq() -> None:
    a = Vec2(Int32(1), Int32(2))
    b = Vec2(Int32(1), Int32(2))
    c = Vec2(Int32(3), Int32(4))
    print(a == b)
    print(a == c)
    print(a != c)

class Tag:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def __eq__(self, other: Tag) -> bool:
        return self.value == other.value

    def __ne__(self, other: Tag) -> bool:
        return self.value != other.value

def test_explicit_ne() -> None:
    a = Tag(Int32(1))
    b = Tag(Int32(1))
    c = Tag(Int32(2))
    print(a != b)
    print(a != c)

class Child(Vec2):
    def __init__(self, x: Int32, y: Int32) -> None:
        super().__init__(x, y)

def test_inherited_eq() -> None:
    a = Child(Int32(1), Int32(2))
    b = Child(Int32(1), Int32(2))
    c = Child(Int32(3), Int32(4))
    print(a == b)
    print(a == c)

def test_comparisons() -> None:
    a = Score(Int32(10))
    b = Score(Int32(20))
    c = Score(Int32(10))
    print(a < b)
    print(a > b)
    print(a <= c)
    print(a >= c)
    print(b > a)
    print(b <= a)

def test_invert() -> None:
    m = Mask(Int32(0))
    inv = ~m
    print(inv.bits)
    m2 = Mask(Int32(5))
    inv2 = ~m2
    print(inv2.bits)

def test_builtin_pos() -> None:
    x: Int32 = Int32(5)
    print(+x)
    y: float = -3.14
    print(+y)

def main() -> None:
    test_unary()
    test_sub_mul()
    test_contains()
    test_iadd()
    test_isub()
    test_hash()
    test_len()
    test_eq()
    test_explicit_ne()
    test_inherited_eq()
    test_comparisons()
    test_invert()
    test_builtin_pos()

main()
