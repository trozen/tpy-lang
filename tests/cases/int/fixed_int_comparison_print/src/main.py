# Printing comparison results of fixed-width integers should output True/False
from tpy import int32, int64, uint32

def test_int32_cmp() -> None:
    a = int32(5)
    b = int32(3)
    print(a > b)
    print(a < b)
    print(a == b)
    print(a != b)
    print(a >= b)
    print(a <= b)

def test_int64_cmp() -> None:
    x = int64(100)
    y = int64(200)
    print(x < y)
    print(x > y)
    print(x == y)

def test_uint32_cmp() -> None:
    m = uint32(10)
    n = uint32(10)
    print(m == n)
    print(m != n)
    print(m >= n)

def main() -> None:
    test_int32_cmp()
    test_int64_cmp()
    test_uint32_cmp()

main()
