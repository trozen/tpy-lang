# tpy.bits -- bit manipulation primitives for fixed-width integers.
#
# Wraps C++20 <bit> (std::rotl/std::rotr) and C++23 <bit> (std::byteswap).
# Rotations don't lose bits by construction; byteswap is endianness-neutral.
# Use these instead of hand-rolled `(x << n) | (x >> (w-n))` which would
# trip TPy's overflow-checked left shift.
# tpy: native_module
# tpy: cpp_namespace("tpy::bits")
from tpy.extern import cpp_template
from tpy import Int32, UInt32, UInt64

@cpp_template("std::rotl<uint32_t>({0}, {1})")
def rotl32(x: UInt32, n: Int32) -> UInt32: ...

@cpp_template("std::rotr<uint32_t>({0}, {1})")
def rotr32(x: UInt32, n: Int32) -> UInt32: ...

@cpp_template("std::rotl<uint64_t>({0}, {1})")
def rotl64(x: UInt64, n: Int32) -> UInt64: ...

@cpp_template("std::rotr<uint64_t>({0}, {1})")
def rotr64(x: UInt64, n: Int32) -> UInt64: ...

@cpp_template("std::byteswap<uint32_t>({0})")
def byteswap32(x: UInt32) -> UInt32: ...

@cpp_template("std::byteswap<uint64_t>({0})")
def byteswap64(x: UInt64) -> UInt64: ...
