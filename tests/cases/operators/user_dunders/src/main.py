# Test user-defined dunder methods on records: binary/unary operators,
# comparisons, __contains__, inplace ops, __hash__, __len__/size()
from __future__ import annotations
from tpy import int32, uint64, Own

class Vec2:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: Vec2) -> Own[Vec2]:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Own[Vec2]:
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: int32) -> Own[Vec2]:
        return Vec2(self.x * scalar, self.y * scalar)

    def __neg__(self) -> Own[Vec2]:
        return Vec2(-self.x, -self.y)

    def __pos__(self) -> Own[Vec2]:
        return Vec2(+self.x, +self.y)

    def __eq__(self, other: Vec2) -> bool:
        return self.x == other.x and self.y == other.y

    def __hash__(self) -> uint64:
        return uint64(self.x * 31 + self.y)

    def __len__(self) -> int32:
        return int32(2)

    def __contains__(self, value: int32) -> bool:
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
    value: int32

    def __init__(self, value: int32) -> None:
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
    bits: int32

    def __init__(self, bits: int32) -> None:
        self.bits = bits

    def __invert__(self) -> Own[Mask]:
        return Mask(~self.bits)

def test_unary() -> None:
    v = Vec2(int32(3), int32(4))
    neg = -v
    print(neg.x)
    print(neg.y)
    pos = +v
    print(pos.x)
    print(pos.y)

def test_contains() -> None:
    v = Vec2(int32(10), int32(20))
    print(int32(10) in v)
    print(int32(20) in v)
    print(int32(99) in v)
    print(int32(99) not in v)
    print(int32(10) not in v)

def test_sub_mul() -> None:
    a = Vec2(int32(5), int32(7))
    b = Vec2(int32(2), int32(3))
    d = a - b
    print(d.x)
    print(d.y)
    s = a * int32(3)
    print(s.x)
    print(s.y)

def test_iadd() -> None:
    v = Vec2(int32(1), int32(2))
    v += Vec2(int32(3), int32(4))
    print(v.x)
    print(v.y)

def test_isub() -> None:
    v = Vec2(int32(10), int32(20))
    v -= Vec2(int32(3), int32(5))
    print(v.x)
    print(v.y)

def test_hash() -> None:
    v = Vec2(int32(1), int32(2))
    h = hash(v)
    print(h > 0)

def test_len() -> None:
    v = Vec2(int32(1), int32(2))
    print(len(v))

def test_eq() -> None:
    a = Vec2(int32(1), int32(2))
    b = Vec2(int32(1), int32(2))
    c = Vec2(int32(3), int32(4))
    print(a == b)
    print(a == c)
    print(a != c)

class Tag:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def __eq__(self, other: Tag) -> bool:
        return self.value == other.value

    def __ne__(self, other: Tag) -> bool:
        return self.value != other.value

def test_explicit_ne() -> None:
    a = Tag(int32(1))
    b = Tag(int32(1))
    c = Tag(int32(2))
    print(a != b)
    print(a != c)

class Child(Vec2):
    def __init__(self, x: int32, y: int32) -> None:
        super().__init__(x, y)

def test_inherited_eq() -> None:
    a = Child(int32(1), int32(2))
    b = Child(int32(1), int32(2))
    c = Child(int32(3), int32(4))
    print(a == b)
    print(a == c)

def test_comparisons() -> None:
    a = Score(int32(10))
    b = Score(int32(20))
    c = Score(int32(10))
    print(a < b)
    print(a > b)
    print(a <= c)
    print(a >= c)
    print(b > a)
    print(b <= a)

def test_invert() -> None:
    m = Mask(int32(0))
    inv = ~m
    print(inv.bits)
    m2 = Mask(int32(5))
    inv2 = ~m2
    print(inv2.bits)

def test_builtin_pos() -> None:
    x: int32 = int32(5)
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
