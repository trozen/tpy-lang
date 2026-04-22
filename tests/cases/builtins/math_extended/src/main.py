# math module: pi, tau, e, inf, hypot, radians, degrees, trunc, atan, atan2,
# cbrt, exp2
import math

def main() -> None:
    # Constants
    print(math.pi > 3.14)
    print(math.pi < 3.15)
    print(math.tau > 6.28)
    print(math.tau < 6.29)
    print(math.e > 2.71)
    print(math.e < 2.72)
    print(math.inf > 1e308)

    # hypot
    h = math.hypot(3.0, 4.0)
    print(h > 4.99)
    print(h < 5.01)

    # radians / degrees round-trip
    r = math.radians(180.0)
    print(r > 3.14)
    print(r < 3.15)
    d = math.degrees(r)
    print(d > 179.99)
    print(d < 180.01)

    # trunc
    print(math.trunc(3.7))
    print(math.trunc(-2.3))

    # atan (single-arg inverse tangent)
    a1 = math.atan(1.0)
    print(a1 > 0.78)
    print(a1 < 0.79)
    print(math.atan(0.0) == 0.0)

    # atan2
    a = math.atan2(1.0, 1.0)
    print(a > 0.78)
    print(a < 0.79)

    # cbrt (cube root) -- Python 3.11+. Use inputs where the cube root is
    # exactly representable in double (|cbrt(x)| a power of 2); cbrt(27) would
    # round to 3.0000000000000004 in CPython's libm.
    print(math.cbrt(8.0) == 2.0)
    print(math.cbrt(-8.0) == -2.0)
    print(math.cbrt(0.0) == 0.0)

    # exp2 (2**x) -- Python 3.11+
    print(math.exp2(10.0) == 1024.0)
    print(math.exp2(0.0) == 1.0)
    print(math.exp2(-1.0) == 0.5)

main()
