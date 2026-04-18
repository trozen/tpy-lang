# Tests for extended math module: trig, hyperbolic, classification, special functions, gcd/lcm/factorial
import math

def main() -> None:
    # tau constant
    print(math.tau > 6.28)
    print(math.tau < 6.29)

    # atan
    a = math.atan(1.0)
    print(a > 0.785)
    print(a < 0.786)

    # Hyperbolic
    print(math.sinh(0.0) == 0.0)
    print(math.cosh(0.0) == 1.0)
    print(math.tanh(0.0) == 0.0)
    s = math.sinh(1.0)
    print(s > 1.17)
    print(s < 1.18)

    # Inverse hyperbolic
    print(math.asinh(0.0) == 0.0)
    print(math.acosh(1.0) == 0.0)
    print(math.atanh(0.0) == 0.0)

    # Float classification
    print(math.isnan(0.0))
    print(math.isnan(float('nan')))
    print(math.isinf(0.0))
    print(math.isinf(float('inf')))
    print(math.isinf(float('-inf')))
    print(math.isfinite(1.0))
    print(math.isfinite(float('inf')))
    print(math.isfinite(float('nan')))

    # log1p / expm1
    print(math.log1p(0.0) == 0.0)
    v = math.expm1(0.0)
    print(v == 0.0)

    # copysign
    print(math.copysign(3.0, -1.0))
    print(math.copysign(-3.0, 1.0))

    # fmod
    r = math.fmod(10.0, 3.0)
    print(r > 0.99)
    print(r < 1.01)

    # erf / erfc
    e = math.erf(0.0)
    print(e == 0.0)
    ec = math.erfc(0.0)
    print(ec == 1.0)

    # gamma / lgamma
    g = math.gamma(5.0)
    print(g > 23.9)
    print(g < 24.1)

    # gcd / lcm
    print(math.gcd(12, 8))
    print(math.gcd(0, 5))
    print(math.gcd(-12, 8))
    print(math.lcm(4, 6))
    print(math.lcm(0, 5))

    # factorial
    print(math.factorial(0))
    print(math.factorial(5))
    print(math.factorial(10))

main()
