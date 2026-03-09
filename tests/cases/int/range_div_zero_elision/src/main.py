# Tests division-by-zero check elision when divisor is provably non-zero.
from tpy import Int32

def test_if_not_zero_floordiv() -> None:
    """if b != 0: a // b -- divisor proven non-zero."""
    a: Int32 = 10
    b: Int32 = 3
    if b != 0:
        x = a // b  # tpyc: div_safe(b)
        print(x)

def test_if_not_zero_mod() -> None:
    """if b != 0: a % b -- same for modulo."""
    a: Int32 = 10
    b: Int32 = 3
    if b != 0:
        x = a % b  # tpyc: div_safe(b)
        print(x)

def test_no_elision_unchecked() -> None:
    """No guard -- division check remains when divisor is not a literal."""
    a: Int32 = 10
    b: Int32 = Int32(3)
    x = a // b  # tpyc: div_checked(b)
    print(x)

def test_assert_not_zero() -> None:
    """assert b != 0 proves non-zero for subsequent code."""
    a: Int32 = 17
    b: Int32 = 5
    assert b != 0
    x = a // b  # tpyc: div_safe(b)
    y = a % b  # tpyc: div_safe(b)
    print(x)
    print(y)

def test_assert_positive() -> None:
    """assert b > 0 proves non-zero (since > 0 implies != 0)."""
    a: Int32 = 20
    b: Int32 = 7
    assert b > 0
    x = a // b  # tpyc: div_safe(b)
    print(x)

def test_no_elision_after_reassign() -> None:
    """Reassignment clears non-zero fact."""
    a: Int32 = 10
    b: Int32 = 3
    assert b != 0
    b = a  # reassignment clears range fact
    x = a // b  # tpyc: div_checked(b)
    print(x)

def test_else_of_eq_zero() -> None:
    """else branch of 'if b == 0' proves b != 0."""
    a: Int32 = 10
    b: Int32 = 3
    if b == 0:
        print("zero")
    else:
        x = a // b  # tpyc: div_safe(b)
        print(x)

def test_literal_divisor() -> None:
    """Literal non-zero divisor is always safe."""
    a: Int32 = 10
    x = a // 3
    y = a % 5
    print(x)
    print(y)

def test_literal_named_divisor() -> None:
    """Literal-initialized non-zero divisor is automatically proven safe."""
    a: Int32 = 10
    b: Int32 = 3
    x = a // b  # tpyc: div_safe(b)
    print(x)

test_if_not_zero_floordiv()
test_if_not_zero_mod()
test_no_elision_unchecked()
test_assert_not_zero()
test_assert_positive()
test_no_elision_after_reassign()
test_else_of_eq_zero()
test_literal_divisor()
test_literal_named_divisor()
