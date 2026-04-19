# math module: special functions (gamma, lgamma, erf, erfc, expm1, log1p),
# float building blocks (nextafter, ldexp, fma), and float decomposition
# (modf, frexp, ulp).
import math

def main() -> None:
    # expm1 / log1p -- exact at 0
    print(math.expm1(0.0) == 0.0)
    print(math.log1p(0.0) == 0.0)
    e1 = math.expm1(1.0)          # e - 1 ~= 1.71828
    print(e1 > 1.71 and e1 < 1.72)
    l1 = math.log1p(1.0)          # ln 2 ~= 0.6931
    print(l1 > 0.69 and l1 < 0.70)

    # gamma / lgamma
    print(math.gamma(5.0))        # 4! = 24
    print(math.gamma(1.0) == 1.0)
    print(math.lgamma(1.0) == 0.0)
    g5 = math.lgamma(5.0)         # ln(24)
    print(g5 > 3.17 and g5 < 3.19)

    # erf / erfc
    print(math.erf(0.0) == 0.0)
    e2 = math.erf(1.0)            # ~= 0.8427
    print(e2 > 0.84 and e2 < 0.85)
    e3 = math.erfc(1.0)           # ~= 0.1573
    print(e3 > 0.15 and e3 < 0.16)
    # erf + erfc round to 1 for moderate x
    print(math.erf(1.0) + math.erfc(1.0) == 1.0)

    # nextafter / ldexp (fma tested separately; added to CPython in 3.13)
    na = math.nextafter(1.0, 2.0)
    print(na > 1.0 and na < 1.0000000001)
    print(math.nextafter(1.0, 1.0) == 1.0)
    print(math.ldexp(1.5, 3))     # 1.5 * 2^3 = 12.0
    print(math.ldexp(1.0, -1))    # 0.5

    # modf: (fractional, integer) both as float
    frac, ip = math.modf(3.75)
    print(frac, ip)
    nfrac, nip = math.modf(-3.75)
    print(nfrac, nip)

    # frexp: (mantissa in [0.5, 1), exponent as T). T defaults to Int32
    # (see math_frexp_generic for explicit Int64/BigInt variants).
    m1, ex1 = math.frexp(12.0)    # 0.75, 4
    print(m1, ex1)
    m2, ex2 = math.frexp(0.5)     # 0.5, 0
    print(m2, ex2)
    m3, ex3 = math.frexp(0.0)     # 0.0, 0
    print(m3, ex3)

    # ulp: always positive; ulp(1.0) ~= 2.22e-16 (IEEE double)
    u1 = math.ulp(1.0)
    print(u1 > 0.0 and u1 < 1e-15)
    u2 = math.ulp(math.inf)
    print(math.isinf(u2))
    # ulp(-x) == ulp(x) (C++ helper takes fabs internally)
    print(math.ulp(-1.0) == math.ulp(1.0))

main()
