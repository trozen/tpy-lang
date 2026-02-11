from tpy import Int32

x = "oops"  # tpyc: error(/incompatible with later annotation 'Int32' at line 4/)
x: Int32 = Int32(1)
