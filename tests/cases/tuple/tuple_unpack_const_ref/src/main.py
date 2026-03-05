# Test const-ref binding for expensive value types in tuple unpack.
# BigInt and String elements should use const ref when not reassigned/augassigned.
from tpy import String

def get_pair() -> tuple[int, int]:
    return (42, 100)

def get_string_pair() -> tuple[String, String]:
    return ("hello", "world")

def test_rvalue_const_ref() -> None:
    """Rvalue source: BigInt elements should bind as const ref."""
    a, b = get_pair()
    print(a)
    print(b)

def test_augassign_no_const_ref() -> None:
    """Augassigned BigInt should use copy, not const ref."""
    a, b = get_pair()
    b += 1
    print(a)
    print(b)

def test_reassign_no_const_ref() -> None:
    """Reassigned BigInt should use copy, not const ref."""
    a, b = get_pair()
    b = 200
    print(a)
    print(b)

def test_lvalue_const_ref() -> None:
    """Lvalue source (named var, not reassigned): safe const ref."""
    t: tuple[int, int] = (10, 20)
    a, b = t
    print(a)
    print(b)

def test_lvalue_reassigned_source() -> None:
    """Lvalue source reassigned later: fall back to copy."""
    t: tuple[int, int] = (10, 20)
    a, b = t
    t = (30, 40)
    print(a)
    print(b)

def test_string_const_ref() -> None:
    """String elements should bind as const ref when read-only."""
    a, b = get_string_pair()
    print(a)
    print(b)

def test_augassign_in_branch() -> None:
    """Aug-assign in a branch conservatively blocks const ref."""
    a, b = get_pair()
    if a > 0:
        b += 1
    print(a)
    print(b)

test_rvalue_const_ref()
test_augassign_no_const_ref()
test_reassign_no_const_ref()
test_lvalue_const_ref()
test_lvalue_reassigned_source()
test_string_const_ref()
test_augassign_in_branch()
