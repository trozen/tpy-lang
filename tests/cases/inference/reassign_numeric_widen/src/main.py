# Numeric widening across reassignments
from tpy import int32, int64, uint8, uint32

def test_int_widen() -> None:
    a = int32(1)  # tpyc: type(int64)
    a = int64(2)  # tpyc: type(int64)
    print(a)

def test_float_absorbs_int() -> None:
    b = int32(1)  # tpyc: type(float)
    b = 1.5  # tpyc: type(float)
    print(b)

def test_float_stays_float() -> None:
    c = 1.5  # tpyc: type(float)
    c = int32(1)  # tpyc: type(float)
    print(c)

def test_bigint_absorbs_fixedint() -> None:
    d = int32(1)  # tpyc: type(int)
    d = int(2)  # tpyc: type(int)
    print(d)

def test_unsigned_to_wider_signed() -> None:
    e = uint8(1)  # tpyc: type(int32)
    e = int32(2)  # tpyc: type(int32)
    print(e)

def test_uint32_to_int64() -> None:
    g = uint32(1)  # tpyc: type(int64)
    g = int64(2)  # tpyc: type(int64)
    print(g)

test_int_widen()
test_float_absorbs_int()
test_float_stays_float()
test_bigint_absorbs_fixedint()
test_unsigned_to_wider_signed()
test_uint32_to_int64()
