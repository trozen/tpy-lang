"""Test min() and max() builtin functions."""
from tpy import Int32

# Test min/max with Int32
a: Int32 = 10
b: Int32 = 20
print(min(a, b))
print(max(a, b))
print(min(b, a))
print(max(b, a))

# Test min/max with BigInt (default int)
x = 100
y = -50
print(min(x, y))
print(max(x, y))
# Use int() for large values to avoid literal issues
big1 = int(-1000000)
big2 = int(1000000)
print(min(big1, big2))
print(max(big1, big2))

# Test min/max with float
f1: float = 3.14
f2: float = 2.71
print(min(f1, f2))
print(max(f1, f2))

# Test 3-argument min/max
c: Int32 = 5
print(min(a, b, c))
print(max(a, b, c))

z = 200
print(min(x, y, z))
print(max(x, y, z))

f3: float = 1.0
print(min(f1, f2, f3))
print(max(f1, f2, f3))
