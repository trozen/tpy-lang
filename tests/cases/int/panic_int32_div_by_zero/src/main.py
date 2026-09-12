"""Test int32 division by zero panic at runtime."""
from tpy import int32

x: int32 = 42
y: int32 = 0
z: int32 = x // y  # Should panic
print(z)
