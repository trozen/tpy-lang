from tpy import int32
from utils import MAX, MIN

# Use imported values first
print(MAX)  # 100
print(MIN)  # 1

# Redefine MAX with same type (explicit annotation)
MAX: int32 = int32(42)

# Redefine MIN with different type (int/BigInt)
MIN: int = 99

# Use local values
print(MAX)  # 42
print(MIN)  # 99
