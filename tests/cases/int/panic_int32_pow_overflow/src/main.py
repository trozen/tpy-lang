"""Test int32 power overflow panic at runtime."""
from tpy import int32

x: int32 = 2
y: int32 = x ** 31  # Should panic - 2^31 overflows int32
print(y)
