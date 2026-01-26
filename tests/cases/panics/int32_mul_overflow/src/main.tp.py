"""Test Int32 multiplication overflow panic at runtime."""
from tpy import Int32

x: Int32 = 2147483647  # INT32_MAX
y: Int32 = x * 2       # Should panic
print(y)
