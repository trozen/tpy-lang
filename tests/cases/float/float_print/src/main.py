import math
from tpy import float32


def show[T](x: T) -> None:
    print("generic", x)


def main():
    # Whole numbers should show .0
    print(1.0)
    print(5.0)
    print(100.0)
    print(-42.0)

    # Simple decimals
    print(0.5)
    print(3.14)
    print(-2.5)

    # The classic 0.1 representation
    print(0.1)
    print(0.2)
    print(0.3)

    # High precision values
    print(3.14159265358979)
    print(2.718281828459045)

    # Large numbers (should not use scientific notation)
    print(123456789.0)
    print(123456789.123456)
    print(9999999999.999999)

    # Small decimals
    print(0.000123)
    print(0.0001)
    print(0.00001)

    # Negative small decimals
    print(-0.000123)
    print(-0.00001)

    # Trailing zeros should be trimmed (but keep at least .X)
    x = 1.10
    print(x)
    y = 2.500
    print(y)

    # Very small numbers
    print(0.000000001)
    print(0.0000000001)

    # Numbers close to integers
    print(1.0000000001)
    print(0.9999999999)

    # str(), concatenation and a bare f-string give the same shortest
    # round-trip text as print()
    y = 0.1 * 3
    print("str", str(y), "a" + str(y), f"{y}")  # tpyc: ok
    print("exp", str(123456789012345678.0), str(1e22), str(1e-05), str(-0.0), str(2.0))
    # non-finite values keep no ".0"
    print("nonfinite", str(math.inf), str(-math.inf), str(math.nan), "x" + str(math.inf))  # tpyc: ok
    f = float32(0.1)
    print("f32", str(f), f"{f}", f)
    # generic print of a float32
    show(f)  # tpyc: ok
    show(y)
    # a precision spec and repr are unchanged
    print("spec", f"{y:.3f}", repr(y))

main()
