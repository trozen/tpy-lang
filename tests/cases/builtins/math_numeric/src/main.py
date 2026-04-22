# math module: float predicates (isnan/isinf/isfinite), copysign, fmod,
# remainder, integer helpers (gcd/lcm/factorial/isqrt/perm/comb), isclose,
# prod/fsum/sumprod/dist.
import math

def main() -> None:
    # isnan / isinf / isfinite on normal values
    print(math.isnan(0.0))
    print(math.isinf(0.0))
    print(math.isfinite(0.0))
    print(math.isnan(1.5))
    print(math.isinf(1.5))
    print(math.isfinite(1.5))

    # isinf / isfinite on infinity
    print(math.isinf(math.inf))
    print(math.isfinite(math.inf))

    # NaN via inf - inf detects as NaN
    nan = math.inf - math.inf
    print(math.isnan(nan))
    print(math.isfinite(nan))

    # math.nan is accessible as a Final[float] constant (constexpr nan)
    print(math.isnan(math.nan))
    print(math.isfinite(math.nan))
    # NaN never equals itself (IEEE 754); isclose(nan, nan) is False in CPython
    print(math.nan == math.nan)
    print(math.isclose(math.nan, math.nan))

    # copysign
    print(math.copysign(3.0, -1.0))
    print(math.copysign(-3.0, 1.0))
    print(math.copysign(0.0, -1.0))

    # fmod (truncation; sign of dividend)
    print(math.fmod(7.5, 2.0))
    print(math.fmod(-7.5, 2.0))

    # remainder (IEEE 754; nearest-even)
    r1 = math.remainder(7.0, 4.0)
    print(r1 > -1.01 and r1 < -0.99)
    r2 = math.remainder(5.0, 4.0)
    print(r2 > 0.99 and r2 < 1.01)

    # gcd
    print(math.gcd(12, 8))
    print(math.gcd(-12, 8))
    print(math.gcd(12, -8))
    print(math.gcd(0, 5))
    print(math.gcd(5, 0))
    print(math.gcd(0, 0))

    # lcm
    print(math.lcm(4, 6))
    print(math.lcm(0, 5))
    print(math.lcm(-4, 6))
    print(math.lcm(4, -6))
    print(math.lcm(-4, -6))
    # lcm with gcd > 1 -- actually exercises the (a // gcd) * b bounded
    # intermediate path. gcd(600000, 1000000) = 200000; naive a * b = 6e11,
    # new impl's intermediate is only 3e6.
    print(math.lcm(600000, 1000000))

    # factorial
    print(math.factorial(0))
    print(math.factorial(1))
    print(math.factorial(5))
    print(math.factorial(10))

    # isqrt
    print(math.isqrt(0))
    print(math.isqrt(1))
    print(math.isqrt(10))
    print(math.isqrt(100))
    print(math.isqrt(1000))

    # perm / comb
    print(math.perm(5, 0))
    print(math.perm(5, 2))
    print(math.perm(5, 5))
    print(math.perm(5, 6))
    # perm(n) one-arg form == factorial(n)
    print(math.perm(0))
    print(math.perm(5))
    print(math.comb(5, 0))
    print(math.comb(5, 2))
    print(math.comb(10, 5))
    print(math.comb(5, 6))

    # isclose
    print(math.isclose(1.0, 1.0))
    print(math.isclose(1.0, 1.0 + 1e-10))
    print(math.isclose(1.0, 1.1))
    print(math.isclose(0.0, 1e-10, abs_tol=1e-8))
    print(math.isclose(math.inf, math.inf))
    print(math.isclose(math.inf, -math.inf))

    # prod
    print(math.prod([2.0, 3.0, 4.0]))
    print(math.prod([1.0, 2.0, 3.0], start=10.0))
    # Empty with explicit float start: CPython returns int 1 without start,
    # but tpy statically dispatches on list[float] and start=1.0 gives 1.0
    # in both environments.
    print(math.prod([], start=1.0))

    # fsum: sum of 10 x 0.1 should be exactly 1.0 with Neumaier
    t = math.fsum([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    print(t > 0.999999 and t < 1.000001)

    # sumprod
    print(math.sumprod([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]))

    # dist
    print(math.dist([0.0, 0.0], [3.0, 4.0]))
    print(math.dist([1.0, 2.0, 3.0], [4.0, 6.0, 3.0]))
    print(math.dist([0.0], [0.0]))
    # n=1 with p[0] > q[0]: verifies the fabs() in the hypot-fold base case.
    print(math.dist([1.0], [3.0]))
    # Overflow safety: naive sqrt(sum((pi-qi)**2)) would overflow since
    # 1e200 * 1e200 == inf. Hypot-fold handles this. std::hypot is required
    # to be faithfully rounded so the exact value is deterministic.
    print(math.dist([1e200, 1e200], [0.0, 0.0]))

main()
