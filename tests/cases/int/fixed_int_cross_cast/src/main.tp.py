# Cross-type casts between fixed-width integer types
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64

# Widening: signed -> larger signed
a: Int8 = Int8(42)
print(Int16(a))
print(Int32(a))
print(Int64(a))

# Widening: unsigned -> larger unsigned
b: UInt8 = UInt8(200)
print(UInt16(b))
print(UInt32(b))
print(UInt64(b))

# Unsigned -> signed (widening)
print(Int16(b))
print(Int32(b))
print(Int64(b))

# Narrowing: larger -> smaller (in range)
c: Int32 = Int32(100)
print(Int8(c))
print(UInt8(c))

# Signed -> unsigned (in range)
d: Int16 = Int16(255)
print(UInt8(d))

# Negative signed -> larger signed
e: Int8 = Int8(-42)
print(Int16(e))
print(Int32(e))
print(Int64(e))
