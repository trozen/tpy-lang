# tpy: cpp_namespace("tpystd::math")
# tpy: include("<tpy/stdlib/math.hpp>")
#
# Pending items to reach full CPython `math` parity. Each is blocked on a
# specific compiler gap tracked in TODO.md / BUGS.md; once fixed, update
# this module.
#
# - Tuple inputs to `prod`, `fsum`, `sumprod`, `dist`. CPython accepts
#   tuples for all four (`math.prod((1.0, 2.0, 3.0))`). Currently fails
#   conformance to `Iterable[float]`. Blocked on the "Tuple iteration +
#   Iterable conformance bundle" TODO.md entry -- needs coordinated sema,
#   codegen, and runtime work (std::tuple has no begin/end).
# - Generic over int type for `gcd`, `isqrt` (result bounded by input, so
#   generic is safe). Currently `int`-only forces BigInt allocation for
#   small-int callers under --default-int=Int32. Pure-TPy generic is
#   blocked on two language gaps: the missing `AnyInt` protocol (BigInt +
#   AnyFixedInt) and the ban on `-x` / `abs(x)` / param reassignment
#   inside unbounded-generic bodies. See TODO.md "No `AnyInt` protocol
#   covering BigInt + all `AnyFixedInt`". `lcm`, `factorial`, `perm`,
#   `comb` should probably stay `int`-only even once that lands -- their
#   results can exceed fixed-int range (e.g. factorial(13) > Int32::max).
#
# Algorithmic follow-ups (correctness under our implementation, but not
# bit-exact or optimal vs CPython):
#
# - `fsum`: currently Neumaier compensated summation. Significantly better
#   than naive but not bit-exact. CPython uses Shewchuk's partials
#   algorithm (maintains a growing list of non-overlapping partials,
#   guarantees the sum rounds correctly to a single double). Worth
#   adopting if users depend on fsum for bit-exact precision.
# - `sumprod`: naive fold. CPython uses compensated arithmetic (similar
#   to fsum) for precision. Large vectors can lose low bits in our impl.
# - `log(x, base)`: always `log(x) / log(base)`. CPython special-cases
#   `base == 2` to dispatch to `log2` (one fewer transcendental call).
#   Minor; consider if benchmarks warrant.

from typing import Final, Iterable, overload
from tpy import Int32
from tpy.extern import native, cpp_template, type_param_default, DefaultInt

pi: Final[float] = 3.141592653589793
tau: Final[float] = 6.283185307179586
e: Final[float] = 2.718281828459045
inf: Final[float] = 1e309
nan: Final[float] = float("nan")

@overload
@native("std::log")
def log(x: float) -> float: ...

@overload
def log(x: float, base: float) -> float:
    return log(x) / log(base)

@native("std::log10")
def log10(x: float) -> float: ...

@native("std::log2")
def log2(x: float) -> float: ...

@native("std::sqrt")
def sqrt(x: float) -> float: ...

@native("std::cbrt")
def cbrt(x: float) -> float: ...

@native("std::pow")
def pow(x: float, y: float) -> float: ...

@native("std::exp")
def exp(x: float) -> float: ...

@native("std::exp2")
def exp2(x: float) -> float: ...

@native("std::expm1")
def expm1(x: float) -> float: ...

@native("std::log1p")
def log1p(x: float) -> float: ...

@native("std::tgamma")
def gamma(x: float) -> float: ...

@native("std::lgamma")
def lgamma(x: float) -> float: ...

@native("std::erf")
def erf(x: float) -> float: ...

@native("std::erfc")
def erfc(x: float) -> float: ...

@native("std::nextafter")
def nextafter(x: float, y: float) -> float: ...

@native("std::ldexp")
def ldexp(x: float, i: Int32) -> float: ...

@native("std::fma")
def fma(x: float, y: float, z: float) -> float: ...

@native("tpy::stdlib::math::modf")
def modf(x: float) -> tuple[float, float]: ...

# Exponent always fits in a 16+ bit signed int (actual range
# [-1073, 1024] for IEEE 754 double; see tpy/stdlib/math.hpp for why).
# Defaults to DefaultInt (respects --default-int); user can pick Int32,
# Int64, BigInt, etc. explicitly. Zero-allocation for fixed-width T;
# BigInt allocates.
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::stdlib::math::frexp<{T}>({0})")
def frexp[T](x: float) -> tuple[float, T]: ...

@native("tpy::stdlib::math::ulp")
def ulp(x: float) -> float: ...

@native("tpy::BigInt::from_floor")
def floor(x: float) -> int: ...

@native("tpy::BigInt::from_ceil")
def ceil(x: float) -> int: ...

@native("std::sin")
def sin(x: float) -> float: ...

@native("std::cos")
def cos(x: float) -> float: ...

@native("std::tan")
def tan(x: float) -> float: ...

@native("std::fabs")
def fabs(x: float) -> float: ...

@native("std::hypot")
def _hypot2(x: float, y: float) -> float: ...

def hypot(*coords: float) -> float:
    if len(coords) == 0:
        return 0.0
    # Hypot-fold is overflow-safe: hypot(hypot(a, b), c) == sqrt(a^2 + b^2 + c^2).
    result = fabs(coords[0])
    for c in coords[1:]:
        result = _hypot2(result, c)
    return result

@native("std::atan2")
def atan2(y: float, x: float) -> float: ...

@native("std::asin")
def asin(x: float) -> float: ...

@native("std::acos")
def acos(x: float) -> float: ...

@native("std::atan")
def atan(x: float) -> float: ...

@native("std::sinh")
def sinh(x: float) -> float: ...

@native("std::cosh")
def cosh(x: float) -> float: ...

@native("std::tanh")
def tanh(x: float) -> float: ...

@native("std::asinh")
def asinh(x: float) -> float: ...

@native("std::acosh")
def acosh(x: float) -> float: ...

@native("std::atanh")
def atanh(x: float) -> float: ...

@native("std::isnan")
def isnan(x: float) -> bool: ...

@native("std::isinf")
def isinf(x: float) -> bool: ...

@native("std::isfinite")
def isfinite(x: float) -> bool: ...

@native("std::copysign")
def copysign(x: float, y: float) -> float: ...

@native("std::fmod")
def fmod(x: float, y: float) -> float: ...

@native("std::remainder")
def remainder(x: float, y: float) -> float: ...

def radians(x: float) -> float:
    return x * (pi / 180.0)

def degrees(x: float) -> float:
    return x * (180.0 / pi)

def _gcd2(a: int, b: int) -> int:
    if a < 0:
        a = -a
    if b < 0:
        b = -b
    while b != 0:
        t: int = b
        b = a % b
        a = t
    return a

def gcd(*ints: int) -> int:
    if len(ints) == 0:
        return 0
    result = ints[0]
    if result < 0:
        result = -result
    for x in ints[1:]:
        if result == 1:
            return 1
        result = _gcd2(result, x)
    return result

def lcm(*ints: int) -> int:
    if len(ints) == 0:
        return 1
    result = ints[0]
    if result < 0:
        result = -result
    for x in ints[1:]:
        if result == 0 or x == 0:
            return 0
        # (a // gcd(a, b)) * b keeps the intermediate bounded by max(|a|, |b|);
        # the naive a*b first would blow up for large BigInts.
        result = (result // _gcd2(result, x)) * x
        if result < 0:
            result = -result
    return result

def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("factorial() not defined for negative values")
    result: int = 1
    i: int = 2
    while i <= n:
        result = result * i
        i = i + 1
    return result

def isqrt(n: int) -> int:
    if n < 0:
        raise ValueError("isqrt() argument must be nonnegative")
    if n == 0:
        return 0
    # Newton's method from a bit-length-derived initial guess: for an
    # n-bit input, sqrt(n) has ~n/2 bits, so 1 << ((bits + 1) // 2) is
    # already an upper bound within a factor of 2 of the answer (factor
    # is sqrt(2) for even bit_length, exactly 2 for odd). Convergence
    # drops from O(log n) iterations (when starting at n) to O(log log n).
    # The `int(1)` LHS forces BigInt arithmetic so the shift can exceed
    # Int32 width without overflow-checking.
    x: int = int(1) << ((n.bit_length() + 1) // 2)
    y: int = (x + n // x) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x

@overload
def perm(n: int) -> int:
    return factorial(n)

@overload
def perm(n: int, k: int) -> int:
    if n < 0 or k < 0:
        raise ValueError("perm() arguments must be non-negative")
    if k > n:
        return 0
    result: int = 1
    i: int = 0
    while i < k:
        result = result * (n - i)
        i = i + 1
    return result

def comb(n: int, k: int) -> int:
    if n < 0 or k < 0:
        raise ValueError("comb() arguments must be non-negative")
    if k > n:
        return 0
    if k > n - k:
        k = n - k
    result: int = 1
    i: int = 0
    while i < k:
        result = result * (n - i) // (i + 1)
        i = i + 1
    return result

def isclose(a: float, b: float, *, rel_tol: float = 1e-09, abs_tol: float = 0.0) -> bool:
    if a == b:
        return True
    if isinf(a) or isinf(b):
        return False
    diff: float = fabs(a - b)
    max_ab: float = fabs(a)
    if fabs(b) > max_ab:
        max_ab = fabs(b)
    return diff <= abs_tol or diff <= rel_tol * max_ab

@overload
def prod(iterable: Iterable[Int32], *, start: Int32 = Int32(1)) -> Int32:
    result: Int32 = start
    for x in iterable:
        result = result * x
    return result

@overload
def prod(iterable: Iterable[int], *, start: int = 1) -> int:
    result: int = start
    for x in iterable:
        result = result * x
    return result

@overload
def prod(iterable: Iterable[float], *, start: float = 1.0) -> float:
    result: float = start
    for x in iterable:
        result = result * x
    return result

def fsum(iterable: Iterable[float]) -> float:
    # Neumaier summation: more accurate than naive + compensated for large swings.
    s: float = 0.0
    c: float = 0.0
    for x in iterable:
        t: float = s + x
        if fabs(s) >= fabs(x):
            c = c + ((s - t) + x)
        else:
            c = c + ((x - t) + s)
        s = t
    return s + c

def sumprod(p: Iterable[float], q: Iterable[float]) -> float:
    # Manual two-iterator drive with length-mismatch detection; CPython uses
    # zip(p, q, strict=True) internally. We don't have strict=True yet, so
    # open-code it.
    s: float = 0.0
    ip = iter(p)
    iq = iter(q)
    while True:
        try:
            a = next(ip)
        except StopIteration:
            try:
                next(iq)
            except StopIteration:
                return s
            raise ValueError("sumprod(): input lengths differ")
        try:
            b = next(iq)
        except StopIteration:
            raise ValueError("sumprod(): input lengths differ")
        s = s + a * b

def dist(p: Iterable[float], q: Iterable[float]) -> float:
    # Fold via hypot to avoid overflow when coordinates are large: the naive
    # sqrt(sum((pi - qi)**2)) overflows when any (pi - qi)**2 exceeds DBL_MAX.
    # std::hypot(a, b) is IEEE overflow-safe, and hypot-folding is
    # mathematically equivalent: hypot(hypot(d0, d1), d2) == sqrt(d0^2 + d1^2 + d2^2).
    ip = iter(p)
    iq = iter(q)
    # Seed with first pair (or return 0.0 for empty inputs).
    try:
        p0 = next(ip)
    except StopIteration:
        try:
            next(iq)
        except StopIteration:
            return 0.0
        raise ValueError("dist(): input lengths differ")
    try:
        q0 = next(iq)
    except StopIteration:
        raise ValueError("dist(): input lengths differ")
    result: float = fabs(p0 - q0)
    while True:
        try:
            pi = next(ip)
        except StopIteration:
            try:
                next(iq)
            except StopIteration:
                return result
            raise ValueError("dist(): input lengths differ")
        try:
            qi = next(iq)
        except StopIteration:
            raise ValueError("dist(): input lengths differ")
        result = _hypot2(result, pi - qi)

@type_param_default(T=DefaultInt)
@cpp_template("::tpy::from_float_check<{T}>({0})")
def trunc[T](x: float) -> T: ...
