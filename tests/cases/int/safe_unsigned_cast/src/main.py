# Tests safe unsigned cast elision when value is provably non-negative.
from tpy import int32, int64, uint32, uint64

def test_assert_non_negative() -> None:
    """assert x >= 0 proves non-negative, int32->uint32 skips range check."""
    x: int32 = 42
    assert x >= 0
    y = uint32(x)  # tpyc: cast_safe(uint32)
    print(y)

def test_if_positive() -> None:
    """if x > 0 proves non-negative, int32->uint64 (widening) skips range check."""
    x: int32 = 10
    if x > 0:
        y = uint64(x)  # tpyc: cast_safe(uint64)
        print(y)

def test_no_elision_unchecked() -> None:
    """No assertion -- cast check remains when value is not a literal."""
    x: int32 = int32(5)
    y = uint32(x)  # tpyc: cast_checked(uint32)
    print(y)

def test_no_elision_narrowing() -> None:
    """int64->uint32 even with assert >= 0: target is narrower, still needs check."""
    x: int64 = 100
    assert x >= 0
    y = uint32(x)  # tpyc: cast_checked(uint32)
    print(y)

def test_no_elision_after_reassign() -> None:
    """Reassignment clears range fact."""
    x: int32 = 10
    assert x >= 0
    x = int32(3)
    y = uint32(x)  # tpyc: cast_checked(uint32)
    print(y)

def test_for_range_index() -> None:
    """Loop variable from range(n) is non-negative, cast to uint32 is safe."""
    n: int32 = 5
    for i in range(n):
        u = uint32(i)  # tpyc: cast_safe(uint32)
        print(u)

def test_int64_to_uint64() -> None:
    """int64->uint64 same-width cast with assert >= 0 is safe."""
    x: int64 = 1000
    assert x >= 0
    y = uint64(x)  # tpyc: cast_safe(uint64)
    print(y)

test_assert_non_negative()
test_if_positive()
test_no_elision_unchecked()
test_no_elision_narrowing()
test_no_elision_after_reassign()
test_for_range_index()
test_int64_to_uint64()
