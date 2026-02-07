"""Test Int32 negation overflow panic at runtime."""
from tpy import Int32

x: Int32 = -2147483648  # INT32_MIN
y: Int32 = -x           # -INT32_MIN overflows, should panic
print(y)
