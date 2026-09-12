# Test round() builtin for float and integer types
from tpy import int32, int64

def main() -> None:
    # round(float) -> default int (int32)
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
    x: int64 = round(9.9)
    print(x)

    # round(int) is identity
    n: int32 = int32(42)
    print(round(n))

    # round(int, ndigits) with negative ndigits
    m: int32 = int32(1250)
    print(round(m, int32(-2)))

main()
