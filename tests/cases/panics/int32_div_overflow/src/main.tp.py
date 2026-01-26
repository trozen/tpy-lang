"""Test Int32 division overflow panic at runtime."""
from tpy import Int32

x: Int32 = -2147483648  # INT32_MIN
y: Int32 = -1
z: Int32 = x // y       # Result would be INT32_MAX + 1, should panic
print(z)
