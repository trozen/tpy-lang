# Bindings declared in another module: a default naming this module's
# `Final` constant is read from here wherever the call omits it.
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Final
from tpy import int32
from tpy.extern import native, cpp_template

LIMIT: Final[int32] = 7


@native("nb::limited")
def limited(x: int32, y: int32 = LIMIT) -> int32: ...


@cpp_template("nb::add2({0}, {1})")
def add2(a: int32, b: int32 = 4) -> int32: ...


@native("nb::generic")
def gadd[T](x: T, y: T = T()) -> T: ...


K: Final[int32] = 3


@native("nb::limited_cast")
def lim(x: int32, y: int32 = int32(K)) -> int32: ...
