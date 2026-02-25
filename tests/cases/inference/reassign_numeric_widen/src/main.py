# Numeric widening across reassignments
from tpy import Int32, Int64, UInt8, UInt32

def test_int_widen() -> None:
    a = Int32(1)  # tpyc: type(Int64)
    a = Int64(2)  # tpyc: type(Int64)
    print(a)

def test_float_absorbs_int() -> None:
    b = Int32(1)  # tpyc: type(float)
    b = 1.5  # tpyc: type(float)
    print(b)

def test_float_stays_float() -> None:
    c = 1.5  # tpyc: type(float)
    c = Int32(1)  # tpyc: type(float)
    print(c)

def test_bigint_absorbs_fixedint() -> None:
    d = Int32(1)  # tpyc: type(int)
    d = int(2)  # tpyc: type(int)
    print(d)

def test_unsigned_to_wider_signed() -> None:
    e = UInt8(1)  # tpyc: type(Int32)
    e = Int32(2)  # tpyc: type(Int32)
    print(e)

def test_uint32_to_int64() -> None:
    g = UInt32(1)  # tpyc: type(Int64)
    g = Int64(2)  # tpyc: type(Int64)
    print(g)

test_int_widen()
test_float_absorbs_int()
test_float_stays_float()
test_bigint_absorbs_fixedint()
test_unsigned_to_wider_signed()
test_uint32_to_int64()
