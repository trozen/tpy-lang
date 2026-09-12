# Test bool() conversion function
from tpy import int32

# Default value
b0: bool = bool()
print(b0)  # False

# From bool
b1: bool = bool(True)
print(b1)  # True
b2: bool = bool(False)
print(b2)  # False

# From int32
b3: bool = bool(int32(0))
print(b3)  # False
b4: bool = bool(int32(1))
print(b4)  # True
b5: bool = bool(int32(-5))
print(b5)  # True

# From int (BigInt)
b6: bool = bool(0)
print(b6)  # False
b7: bool = bool(42)
print(b7)  # True
b8: bool = bool(-100)
print(b8)  # True
