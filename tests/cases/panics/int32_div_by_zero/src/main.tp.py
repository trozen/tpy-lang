"""Test Int32 division by zero panic at runtime."""
from tpy import Int32

x: Int32 = 42
y: Int32 = 0
z: Int32 = x // y  # Should panic
print(z)
