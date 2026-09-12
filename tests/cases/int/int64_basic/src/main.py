# Test int64 and uint64 basic operations
from tpy import int64, uint64

def main() -> None:
    # int64 constructors and large values
    a: int64 = int64(9223372036854775807)  # Max int64
    print(a)

    b: int64 = int64(-9223372036854775808)  # Min int64
    print(b)

    # int64 arithmetic
    x: int64 = int64(1000000000)
    y: int64 = int64(2000000000)
    print(x + y)
    print(x * int64(3))

    # uint64 constructors
    c: uint64 = uint64(0)
    d: uint64 = uint64(18446744073709551615)  # Max uint64
    print(c)
    print(d)

    # uint64 arithmetic
    u: uint64 = uint64(10000000000)
    v: uint64 = uint64(5000000000)
    print(u + v)
    print(u - v)

    # Conversion to BigInt
    big: int = int(a)
    print(big)

main()
