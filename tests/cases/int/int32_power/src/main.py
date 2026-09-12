"""Test int32 power operator."""
from tpy import int32

# Basic power
x: int32 = 2
print(x ** 10)  # 1024

# Power with literal exponent
y: int32 = 3
print(y ** 4)   # 81

# Power of 0
z: int32 = 5
print(z ** 0)   # 1

# Power of 1
print(x ** 1)   # 2

# Negative base
n: int32 = -2
print(n ** 3)   # -8
print(n ** 4)   # 16
