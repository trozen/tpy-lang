# Truncating (wrapping) casts between fixed-width integer types
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64

# Overflow wraps (no panic)
x: Int32 = Int32(300)
print(UInt8.trunc(x))   # 300 % 256 = 44

# Negative to unsigned wraps
print(UInt8.trunc(Int8(-1)))   # 255
print(UInt8.trunc(Int8(-3)))   # 253

# Unsigned to smaller unsigned
print(UInt8.trunc(UInt16(1000)))  # 1000 % 256 = 232

# Large to small signed
print(Int8.trunc(Int32(200)))  # -56 (wraps)

# Unsigned to signed
print(Int8.trunc(UInt8(200)))  # -56

# In-range values pass through unchanged
print(UInt8.trunc(Int32(42)))   # 42
print(Int8.trunc(Int16(-100)))  # -100

# BigInt truncation
print(UInt8.trunc(300))    # 44
print(UInt8.trunc(-1))     # 255
print(Int8.trunc(-129))    # 127
