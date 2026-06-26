# Shared across the ext-exec and cpy-parity runs: in-range boundary values per
# width round-trip identically (they fit every representation, so the .so's
# range check is a no-op and the unbounded source agrees). Out-of-range and
# negative-into-unsigned cases diverge and live in ext_checks.py.
import int_widths as m

print(m.i8(-128), m.i8(0), m.i8(127))
print(m.u8(0), m.u8(255))
print(m.i16(-32768), m.i16(32767))
print(m.u16(0), m.u16(65535))
print(m.i32(-2147483648), m.i32(2147483647))
print(m.u32(0), m.u32(4294967295))
print(m.i64(-9223372036854775808), m.i64(9223372036854775807))
print(m.u64(0), m.u64(18446744073709551615))
