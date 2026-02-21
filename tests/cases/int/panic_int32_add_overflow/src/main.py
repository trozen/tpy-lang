"""Test Int32 addition overflow panic at runtime."""
from tpy import Int32

x: Int32 = 2147483647  # INT32_MAX
y: Int32 = x + 1       # Should panic
print(y)
