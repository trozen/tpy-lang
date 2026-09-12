# Test int(str) with invalid input - should panic
from tpy import int32

x: int = int("abc")
print(x)  # Should not reach here
