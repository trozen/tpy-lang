# Test type constructor conversions across all primitive types
from tpy import Int32, Char

# --- Int32 constructors ---
# Int32(float): truncate toward zero
print(Int32(3.7))    # 3
print(Int32(-3.7))   # -3
print(Int32(0.0))    # 0

# Int32(str): parse
print(Int32("42"))    # 42
print(Int32("-100"))  # -100
print(Int32(" 7 "))   # 7 (whitespace stripped)

# Int32(bool)
print(Int32(True))    # 1
print(Int32(False))   # 0

# --- int constructors ---
# int(bool)
print(int(True))      # 1
print(int(False))     # 0

# int(Char)
print(int(chr(65)))   # 65 (ASCII 'A')
print(int(chr(0)))    # 0

# --- float constructors ---
# float(bool)
print(float(True))    # 1.0
print(float(False))   # 0.0

# float(str)
print(float("3.14"))   # 3.14
print(float("-0.5"))   # -0.5
print(float(" 42 "))   # 42.0
print(float("1e3"))    # 1000.0
print(float("inf"))    # inf
print(float("-inf"))   # -inf

# --- bool constructors ---
# bool(float)
print(bool(0.0))      # False
print(bool(1.5))      # True
print(bool(-0.1))     # True

# bool(str)
print(bool(""))        # False
print(bool("hello"))   # True
print(bool(" "))       # True (non-empty)
