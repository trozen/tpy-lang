# Cross-type casts between fixed-width integer types
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64

# Widening: signed -> larger signed
a: int8 = int8(42)
print(int16(a))
print(int32(a))
print(int64(a))

# Widening: unsigned -> larger unsigned
b: uint8 = uint8(200)
print(uint16(b))
print(uint32(b))
print(uint64(b))

# Unsigned -> signed (widening)
print(int16(b))
print(int32(b))
print(int64(b))

# Narrowing: larger -> smaller (in range)
c: int32 = int32(100)
print(int8(c))
print(uint8(c))

# Signed -> unsigned (in range)
d: int16 = int16(255)
print(uint8(d))

# Negative signed -> larger signed
e: int8 = int8(-42)
print(int16(e))
print(int32(e))
print(int64(e))
