# Test Int64 and UInt64 basic operations
from tpy import Int64, UInt64

def main() -> None:
    # Int64 constructors and large values
    a: Int64 = Int64(9223372036854775807)  # Max Int64
    print(a)

    b: Int64 = Int64(-9223372036854775808)  # Min Int64
    print(b)

    # Int64 arithmetic
    x: Int64 = Int64(1000000000)
    y: Int64 = Int64(2000000000)
    print(x + y)
    print(x * Int64(3))

    # UInt64 constructors
    c: UInt64 = UInt64(0)
    d: UInt64 = UInt64(18446744073709551615)  # Max UInt64
    print(c)
    print(d)

    # UInt64 arithmetic
    u: UInt64 = UInt64(10000000000)
    v: UInt64 = UInt64(5000000000)
    print(u + v)
    print(u - v)

    # Conversion to BigInt
    big: int = int(a)
    print(big)

main()
