# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from typing import overload, Sized
from tpy import Int32, UInt64, Char, String, StrView, pure, readonly
from tpy import Hashable, Representable
from tpy.extern import native, cpp_template


@pure
@readonly
@native("tpy::__len__")
def len(x: Sized) -> Int32: ...


@pure
@readonly
@cpp_template("std::string(::tpy::__repr__({0}))")
def repr(x: Representable) -> str: ...


@pure
@readonly
@native("tpy::__hash__")
def hash(x: Hashable) -> UInt64: ...


@overload
@pure
@readonly
@cpp_template("static_cast<char>({0})")
def chr(i: Int32) -> Char: ...

@overload
@pure
@readonly
@cpp_template("static_cast<char>(({0}).to_fixed_check<int32_t>())")
def chr(i: int) -> Char: ...


@overload
@pure
@readonly
@cpp_template("static_cast<int32_t>(static_cast<unsigned char>({0}))")
def ord(c: Char) -> Int32: ...

@overload
@readonly
@native("tpy::ord_str")
def ord(s: str) -> Int32: ...

@overload
@readonly
@native("tpy::ord_str")
def ord(s: String) -> Int32: ...

@overload
@readonly
@native("tpy::ord_str")
def ord(s: StrView) -> Int32: ...


@overload
@pure
@readonly
@native("std::abs")
def abs(x: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("tpy::BigInt::abs")
def abs(x: int) -> int: ...

@overload
@pure
@readonly
@native("std::fabs")
def abs(x: float) -> float: ...


@overload
@pure
@readonly
@native("std::min")
def min(a: Int32, b: Int32) -> Int32: ...

@overload
@pure
@readonly
@cpp_template("std::min(std::min({0}, {1}), {2})")
def min(a: Int32, b: Int32, c: Int32) -> Int32: ...

@overload
@pure
@readonly
@cpp_template("(({0}) < ({1}) ? ({0}) : ({1}))")
def min(a: int, b: int) -> int: ...

@overload
@pure
@readonly
@native("tpy::bigint_min")
def min(a: int, b: int, c: int) -> int: ...

@overload
@pure
@readonly
@native("std::fmin")
def min(a: float, b: float) -> float: ...

@overload
@pure
@readonly
@cpp_template("std::fmin(std::fmin({0}, {1}), {2})")
def min(a: float, b: float, c: float) -> float: ...


@overload
@pure
@readonly
@native("std::max")
def max(a: Int32, b: Int32) -> Int32: ...

@overload
@pure
@readonly
@cpp_template("std::max(std::max({0}, {1}), {2})")
def max(a: Int32, b: Int32, c: Int32) -> Int32: ...

@overload
@pure
@readonly
@cpp_template("(({0}) > ({1}) ? ({0}) : ({1}))")
def max(a: int, b: int) -> int: ...

@overload
@pure
@readonly
@native("tpy::bigint_max")
def max(a: int, b: int, c: int) -> int: ...

@overload
@pure
@readonly
@native("std::fmax")
def max(a: float, b: float) -> float: ...

@overload
@pure
@readonly
@cpp_template("std::fmax(std::fmax({0}, {1}), {2})")
def max(a: float, b: float, c: float) -> float: ...
