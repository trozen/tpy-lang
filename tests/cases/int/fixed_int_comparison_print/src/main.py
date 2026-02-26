# Printing comparison results of fixed-width integers should output True/False
from tpy import Int32, Int64, UInt32

def test_int32_cmp() -> None:
    a = Int32(5)
    b = Int32(3)
    print(a > b)
    print(a < b)
    print(a == b)
    print(a != b)
    print(a >= b)
    print(a <= b)

def test_int64_cmp() -> None:
    x = Int64(100)
    y = Int64(200)
    print(x < y)
    print(x > y)
    print(x == y)

def test_uint32_cmp() -> None:
    m = UInt32(10)
    n = UInt32(10)
    print(m == n)
    print(m != n)
    print(m >= n)

def main() -> None:
    test_int32_cmp()
    test_int64_cmp()
    test_uint32_cmp()

main()
