# Test bool() conversion function
from tpy import Int32, Bool

# Default value
b0: Bool = bool()
print(b0)  # False

# From bool
b1: Bool = bool(True)
print(b1)  # True
b2: Bool = bool(False)
print(b2)  # False

# From Int32
b3: Bool = bool(Int32(0))
print(b3)  # False
b4: Bool = bool(Int32(1))
print(b4)  # True
b5: Bool = bool(Int32(-5))
print(b5)  # True

# From int (BigInt)
b6: Bool = bool(0)
print(b6)  # False
b7: Bool = bool(42)
print(b7)  # True
b8: Bool = bool(-100)
print(b8)  # True
