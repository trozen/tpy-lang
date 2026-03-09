# Test Box[T] operators: __eq__, __lt__/__le__/__gt__/__ge__, __hash__
from tpy import Int32
from tplib import Box

def test_eq() -> None:
    a = Box(Int32(1))
    b = Box(Int32(1))
    c = Box(Int32(2))
    print(a == b)
    print(a == c)
    print(a != b)
    print(a != c)

def test_comparisons() -> None:
    a = Box(Int32(1))
    b = Box(Int32(2))
    c = Box(Int32(1))
    print(a < b)
    print(b < a)
    print(a <= c)
    print(a <= b)
    print(b > a)
    print(a > b)
    print(a >= c)
    print(b >= a)

def test_hash() -> None:
    a = Box(Int32(42))
    b = Box(Int32(42))
    print(hash(a) == hash(b))

def main() -> None:
    test_eq()
    test_comparisons()
    test_hash()

main()
