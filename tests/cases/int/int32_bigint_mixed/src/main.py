# Mixed int32 and BigInt arithmetic: explicit int32 combined with
# unannotated default-int or out-of-range literals.
from tpy import int32


def test_int32_plus_bigint():
    """int32 + BigInt should promote to BigInt."""
    x: int32 = 5
    y = 10  # default int (int32)
    z = x + y  # Should be BigInt(15)
    print(z)


def test_bigint_plus_int32():
    """BigInt + int32 should be BigInt."""
    x = 10  # default int (int32)
    y: int32 = 5
    z = x + y  # Should be BigInt(15)
    print(z)


def test_mixed_arithmetic():
    """Various mixed operations."""
    a: int32 = 20
    b = 3  # default int (int32)

    print(a - b)   # 17
    print(a * b)   # 60
    print(a // b)  # 6
    print(a % b)   # 2


def test_large_bigint():
    """int32 + large BigInt must not overflow."""
    x: int32 = 5
    y = 10 ** 20  # tpyc: warning(/outside default int32 range/)
    z = x + y     # Must promote to BigInt, not panic
    print(z)


def test_augmented_assign_mixed():
    """int32 augmented assignment with BigInt converts to int32."""
    x: int32 = 100
    b = 7  # default int (int32)

    x += b
    print(x)  # 107

    x -= b
    print(x)  # 100

    x *= b
    print(x)  # 700

    x //= b
    print(x)  # 100

    x %= b
    print(x)  # 2


def test_nested_literal_binop():
    """Nested literal binops assigned to int32 should use int32 arithmetic."""
    x: int32 = 1 + (2 + 3)
    print(x)  # 6

    y: int32 = (1 + 2) * (3 + 4)
    print(y)  # 21

    # Assignment to existing int32 variable
    z: int32 = 0
    z = 10 + (20 + 30)
    print(z)  # 60


test_int32_plus_bigint()
test_bigint_plus_int32()
test_mixed_arithmetic()
test_large_bigint()
test_augmented_assign_mixed()
test_nested_literal_binop()
