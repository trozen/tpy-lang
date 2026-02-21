# Mixed Int32 and BigInt arithmetic: explicit Int32 combined with
# unannotated default-int or out-of-range literals.
from tpy import Int32


def test_int32_plus_bigint():
    """Int32 + BigInt should promote to BigInt."""
    x: Int32 = 5
    y = 10  # default int (Int32)
    z = x + y  # Should be BigInt(15)
    print(z)


def test_bigint_plus_int32():
    """BigInt + Int32 should be BigInt."""
    x = 10  # default int (Int32)
    y: Int32 = 5
    z = x + y  # Should be BigInt(15)
    print(z)


def test_mixed_arithmetic():
    """Various mixed operations."""
    a: Int32 = 20
    b = 3  # default int (Int32)

    print(a - b)   # 17
    print(a * b)   # 60
    print(a // b)  # 6
    print(a % b)   # 2


def test_large_bigint():
    """Int32 + large BigInt must not overflow."""
    x: Int32 = 5
    y = 10 ** 20  # tpyc: warning(/outside default Int32 range/)
    z = x + y     # Must promote to BigInt, not panic
    print(z)


def test_augmented_assign_mixed():
    """Int32 augmented assignment with BigInt converts to Int32."""
    x: Int32 = 100
    b = 7  # default int (Int32)

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
    """Nested literal binops assigned to Int32 should use Int32 arithmetic."""
    x: Int32 = 1 + (2 + 3)
    print(x)  # 6

    y: Int32 = (1 + 2) * (3 + 4)
    print(y)  # 21

    # Assignment to existing Int32 variable
    z: Int32 = 0
    z = 10 + (20 + 30)
    print(z)  # 60


test_int32_plus_bigint()
test_bigint_plus_int32()
test_mixed_arithmetic()
test_large_bigint()
test_augmented_assign_mixed()
test_nested_literal_binop()
