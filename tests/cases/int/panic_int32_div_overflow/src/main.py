"""Test int32 division overflow panic at runtime."""
from tpy import int32

x: int32 = -2147483648  # INT32_MIN
y: int32 = -1
z: int32 = x // y       # Result would be INT32_MAX + 1, should panic
print(z)
