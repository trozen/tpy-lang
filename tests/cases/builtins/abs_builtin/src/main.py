"""Test abs() builtin function for int32, BigInt, and float."""
from tpy import int32

# Test abs with int32
x: int32 = -42
print(abs(x))
print(abs(int32(10)))
print(abs(int32(0)))

# Test abs with BigInt (default int)
y = -100
print(abs(y))
print(abs(99))
# Large BigInt
big = int(-1000000)
print(abs(big))

# Test abs with float
z: float = -3.14
print(abs(z))
print(abs(2.5))
print(abs(-0.0))
