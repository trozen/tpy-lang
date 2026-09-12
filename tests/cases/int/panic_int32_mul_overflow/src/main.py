"""Test int32 multiplication overflow panic at runtime."""
from tpy import int32

x: int32 = 2147483647  # INT32_MAX
y: int32 = x * 2       # Should panic
print(y)
