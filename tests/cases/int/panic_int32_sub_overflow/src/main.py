"""Test int32 subtraction overflow panic at runtime."""
from tpy import int32

x: int32 = -2147483648  # INT32_MIN
y: int32 = x - 1        # Should panic
print(y)
