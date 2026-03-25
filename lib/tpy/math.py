# tpy: native_module(forward=True)
# tpy: cpp_namespace("tpystd::math")
# tpy: include("<tpy/math_ops.hpp>")
from typing import overload
from tpy.extern import native

@overload
@native("std::log")
def log(x: float) -> float: ...

@overload
@native("tpy::math::log_base")
def log(x: float, base: float) -> float: ...

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
