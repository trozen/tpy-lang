# Pointer None checks use identity operators; != None stays invalid.
from tpy import Ptr, Int32

p: Ptr[Int32] = None
print(p != None)  # tpyc: error(/Use 'is None' \/ 'is not None' for pointer None checks/)
