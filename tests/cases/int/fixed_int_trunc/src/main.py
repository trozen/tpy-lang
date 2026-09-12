# Truncating (wrapping) casts between fixed-width integer types
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64

# Overflow wraps (no panic)
x: int32 = int32(300)
print(uint8.trunc(x))   # 300 % 256 = 44

# Negative to unsigned wraps
print(uint8.trunc(int8(-1)))   # 255
print(uint8.trunc(int8(-3)))   # 253

# Unsigned to smaller unsigned
print(uint8.trunc(uint16(1000)))  # 1000 % 256 = 232

# Large to small signed
print(int8.trunc(int32(200)))  # -56 (wraps)

# Unsigned to signed
print(int8.trunc(uint8(200)))  # -56

# In-range values pass through unchanged
print(uint8.trunc(int32(42)))   # 42
print(int8.trunc(int16(-100)))  # -100

# BigInt truncation
print(uint8.trunc(300))    # 44
print(uint8.trunc(-1))     # 255
print(int8.trunc(-129))    # 127
