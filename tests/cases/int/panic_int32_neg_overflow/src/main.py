"""Test int32 negation overflow panic at runtime."""
from tpy import int32

x: int32 = -2147483648  # INT32_MIN
y: int32 = -x           # -INT32_MIN overflows, should panic
print(y)
