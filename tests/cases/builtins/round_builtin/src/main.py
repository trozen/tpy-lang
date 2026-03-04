# Test round() builtin for float and integer types
from tpy import Int32, Int64

def main() -> None:
    # round(float) -> default int (Int32)
    print(round(3.7))
    print(round(-1.5))
    print(round(0.5))

    # Banker's rounding (round half to even)
    print(round(2.5))
    print(round(3.5))
    print(round(4.5))

    # round with ndigits
    print(round(3.14159, 2))
    print(round(2.71828, 3))

    # round with context inference
    x: Int64 = round(9.9)
    print(x)

    # round(int) is identity
    n: Int32 = Int32(42)
    print(round(n))

    # round(int, ndigits) with negative ndigits
    m: Int32 = Int32(1250)
    print(round(m, Int32(-2)))

main()
