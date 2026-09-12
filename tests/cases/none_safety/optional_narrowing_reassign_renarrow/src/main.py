# Reassignment of Optional interacts correctly with narrowing.
from tpy import int32

def test_reassign_renarrows(x: int32 | None) -> int32:
    """Assigning a non-None value re-narrows the variable."""
    x = 10
    return x + 1

def test_truthiness_short_circuit(a: int32 | None, b: int32 | None) -> int32:
    """Truthiness narrowing flows through && short-circuit."""
    if a and b:
        return a + b
    return 0

def test_is_not_none_short_circuit(a: int32 | None, b: int32 | None) -> int32:
    """is-not-None narrowing flows through && short-circuit."""
    if a is not None and b is not None:
        return a + b
    return 0

print(test_reassign_renarrows(None))
print(test_truthiness_short_circuit(3, 4))
print(test_truthiness_short_circuit(None, 4))
print(test_is_not_none_short_circuit(3, 4))
print(test_is_not_none_short_circuit(None, 4))
