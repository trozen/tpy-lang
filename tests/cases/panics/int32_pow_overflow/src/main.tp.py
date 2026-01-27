"""Test Int32 power overflow panic at runtime."""
from tpy import Int32

x: Int32 = 2
y: Int32 = x ** 31  # Should panic - 2^31 overflows Int32
print(y)
