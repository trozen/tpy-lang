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
