from tpy import int32
from utils import MAX

# Redefine the imported MAX - this should work and be used
MAX: int32 = int32(42)

# Top-level print uses the redefined MAX
print(MAX)  # Should print 42
