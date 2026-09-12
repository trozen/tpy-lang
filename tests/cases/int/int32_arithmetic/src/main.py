"""Test int32 arithmetic operations with overflow checks."""
from tpy import int32


def test_binary_ops():
    """Test binary arithmetic operations."""
    a: int32 = 20
    b: int32 = 7

    # Addition
    print(a + b)  # 27

    # Subtraction
    print(a - b)  # 13

    # Multiplication
    print(a * b)  # 140

    # Division (floor)
    print(a // b)  # 2

    # Modulo
    print(a % b)  # 6


def test_unary_neg():
    """Test unary negation."""
    x: int32 = 42
    print(-x)  # -42

    y: int32 = -100
    print(-y)  # 100


def test_mixed_literals():
    """Test int32 with literal operands."""
    x: int32 = 10

    # int32 + literal
    print(x + 5)  # 15

    # literal + int32 (less common but should work)
    print(5 + x)  # 15

    # Chained operations
    print(x * 2 + 3)  # 23


def test_negative_division():
    """Test division with negative numbers (Python floor semantics)."""
    a: int32 = -17
    b: int32 = 5

    # Python floor division: -17 // 5 = -4 (not -3)
    print(a // b)  # -4

    # Python modulo: -17 % 5 = 3 (not -2)
    print(a % b)  # 3


test_binary_ops()
test_unary_neg()
test_mixed_literals()
test_negative_division()
