# Test int(str) conversion function
from tpy import int32

# Basic parsing
a: int = int("42")
print(a)  # 42

b: int = int("-123")
print(b)  # -123

c: int = int("+456")
print(c)  # 456

# Zero
d: int = int("0")
print(d)  # 0

# Leading/trailing whitespace
e: int = int("  789  ")
print(e)  # 789

f: int = int("  -99  ")
print(f)  # -99

# Large numbers
g: int = int("12345678901234567890")
print(g)  # 12345678901234567890

h: int = int("-12345678901234567890")
print(h)  # -12345678901234567890
