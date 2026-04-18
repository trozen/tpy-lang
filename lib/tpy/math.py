# tpy: cpp_namespace("tpystd::math")
from typing import Final, overload
from tpy.extern import native, cpp_template, type_param_default, DefaultInt

pi: Final[float] = 3.141592653589793
e: Final[float] = 2.718281828459045
inf: Final[float] = 1e309

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

@native("std::pow")
def pow(x: float, y: float) -> float: ...

@native("std::exp")
def exp(x: float) -> float: ...

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
def hypot(x: float, y: float) -> float: ...

@native("std::atan2")
def atan2(y: float, x: float) -> float: ...

@native("std::asin")
def asin(x: float) -> float: ...

@native("std::acos")
def acos(x: float) -> float: ...

def radians(x: float) -> float:
    return x * (pi / 180.0)

def degrees(x: float) -> float:
    return x * (180.0 / pi)

@type_param_default(T=DefaultInt)
@cpp_template("::tpy::from_float_check<{T}>({0})")
def trunc[T](x: float) -> T: ...

# Additional constants
tau: Final[float] = 6.283185307179586

# Angle
@native("std::atan")
def atan(x: float) -> float: ...

# Hyperbolic
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

# Float classification
@native("std::isnan")
def isnan(x: float) -> bool: ...

@native("std::isinf")
def isinf(x: float) -> bool: ...

@native("std::isfinite")
def isfinite(x: float) -> bool: ...

# Logarithmic / exponential
@native("std::log1p")
def log1p(x: float) -> float: ...

@native("std::expm1")
def expm1(x: float) -> float: ...

# Floating-point ops
@native("std::copysign")
def copysign(x: float, y: float) -> float: ...

@native("std::remainder")
def remainder(x: float, y: float) -> float: ...

@native("std::fmod")
def fmod(x: float, y: float) -> float: ...

# Special functions
@native("std::erf")
def erf(x: float) -> float: ...

@native("std::erfc")
def erfc(x: float) -> float: ...

@native("std::tgamma")
def gamma(x: float) -> float: ...

@native("std::lgamma")
def lgamma(x: float) -> float: ...

# Integer math (pure Python for BigInt compatibility)
def gcd(a: int, b: int) -> int:
    a = abs(a)
    b = abs(b)
    while b != 0:
        a, b = b, a % b
    return a

def lcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return abs(a // gcd(a, b) * b)

def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("factorial() not defined for negative values")
    result: int = 1
    i: int = 2
    while i <= n:
        result *= i
        i += 1
    return result
