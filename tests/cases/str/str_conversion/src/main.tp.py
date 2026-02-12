# Test str() conversion function
from tpy import Int32, Char

# Empty string
print(str())  # empty

# From str (identity)
print(str("hello"))  # hello

# From bool (static strings - safe)
print(str(True))   # True
print(str(False))  # False

# From Char (static lookup - safe)
c: Char = "A"
print(str(c))  # A

# From Int32 (inline usage - safe)
print(str(Int32(42)))    # 42
print(str(Int32(-123)))  # -123
print(str(Int32(0)))     # 0

# From int/BigInt (inline usage - safe)
print(str(12345))         # 12345
print(str(-99999))        # -99999

# From float (inline usage - safe)
# Note: exact output format may vary
x: float = 3.14
print(str(x))  # 3.140000
