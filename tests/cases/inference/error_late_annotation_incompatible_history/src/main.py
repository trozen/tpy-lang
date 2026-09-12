from tpy import int32

x = "oops"  # tpyc: error(/incompatible with later annotation 'int32' at line 4/)
x: int32 = int32(1)
