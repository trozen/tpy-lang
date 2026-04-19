# math module: hyperbolic functions (sinh/cosh/tanh) and their inverses
import math

def main() -> None:
    # Values at 0 -- exact
    print(math.sinh(0.0) == 0.0)
    print(math.cosh(0.0) == 1.0)
    print(math.tanh(0.0) == 0.0)

    # sinh(1) = (e - 1/e) / 2 ~= 1.1752
    s = math.sinh(1.0)
    print(s > 1.17 and s < 1.18)

    # cosh(1) = (e + 1/e) / 2 ~= 1.5430
    c = math.cosh(1.0)
    print(c > 1.54 and c < 1.55)

    # tanh grows towards 1 for large x
    t = math.tanh(10.0)
    print(t > 0.9999)

    # Round-trips through inverses
    print(math.asinh(0.0) == 0.0)
    print(math.acosh(1.0) == 0.0)
    print(math.atanh(0.0) == 0.0)

    r1 = math.asinh(math.sinh(0.5))
    print(r1 > 0.49 and r1 < 0.51)

    r2 = math.acosh(math.cosh(1.5))
    print(r2 > 1.49 and r2 < 1.51)

    r3 = math.atanh(math.tanh(0.3))
    print(r3 > 0.29 and r3 < 0.31)

main()
