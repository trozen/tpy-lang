# Tests safe unsigned cast elision when value is provably non-negative.
from tpy import Int32, Int64, UInt32, UInt64

def test_assert_non_negative() -> None:
    """assert x >= 0 proves non-negative, Int32->UInt32 skips range check."""
    x: Int32 = 42
    assert x >= 0
    y = UInt32(x)  # tpyc: cast_safe(UInt32)
    print(y)

def test_if_positive() -> None:
    """if x > 0 proves non-negative, Int32->UInt64 (widening) skips range check."""
    x: Int32 = 10
    if x > 0:
        y = UInt64(x)  # tpyc: cast_safe(UInt64)
        print(y)

def test_no_elision_unchecked() -> None:
    """No assertion -- cast check remains when value is not a literal."""
    x: Int32 = Int32(5)
    y = UInt32(x)  # tpyc: cast_checked(UInt32)
    print(y)

def test_no_elision_narrowing() -> None:
    """Int64->UInt32 even with assert >= 0: target is narrower, still needs check."""
    x: Int64 = 100
    assert x >= 0
    y = UInt32(x)  # tpyc: cast_checked(UInt32)
    print(y)

def test_no_elision_after_reassign() -> None:
    """Reassignment clears range fact."""
    x: Int32 = 10
    assert x >= 0
    x = Int32(3)
    y = UInt32(x)  # tpyc: cast_checked(UInt32)
    print(y)

def test_for_range_index() -> None:
    """Loop variable from range(n) is non-negative, cast to UInt32 is safe."""
    n: Int32 = 5
    for i in range(n):
        u = UInt32(i)  # tpyc: cast_safe(UInt32)
        print(u)

def test_int64_to_uint64() -> None:
    """Int64->UInt64 same-width cast with assert >= 0 is safe."""
    x: Int64 = 1000
    assert x >= 0
    y = UInt64(x)  # tpyc: cast_safe(UInt64)
    print(y)

test_assert_non_negative()
test_if_positive()
test_no_elision_unchecked()
test_no_elision_narrowing()
test_no_elision_after_reassign()
test_for_range_index()
test_int64_to_uint64()
