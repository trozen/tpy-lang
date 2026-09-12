"""Test int32 addition overflow panic at runtime."""
from tpy import int32

x: int32 = 2147483647  # INT32_MAX
y: int32 = x + 1       # Should panic
print(y)
