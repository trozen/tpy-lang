# Pointer None checks use identity operators; equality with None stays invalid.
from tpy import Ptr, int32

p: Ptr[int32] = None
print(p == None)  # tpyc: error(/Use 'is None' \/ 'is not None' for pointer None checks/)
