# Test that tpy types require explicit import
x: Int32 = Int32(42)  # tpyc: error(/Int32.*requires.*from tpy import/)
