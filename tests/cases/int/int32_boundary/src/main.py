"""Test valid Int32 operations at boundary values."""
from tpy import Int32

# INT32_MAX operations that don't overflow
x: Int32 = 2147483647
print(x)
print(x - 1)
print(x // 2)

# INT32_MIN operations that don't overflow
y: Int32 = -2147483648
print(y)
print(y + 1)
print(y // 2)
