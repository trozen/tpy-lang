from tpy import Int32
from utils import MAX

# Redefine the imported MAX - this should work and be used
MAX: Int32 = Int32(42)

# Top-level print uses the redefined MAX
print(MAX)  # Should print 42
