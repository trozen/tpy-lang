# Test that tpy types require explicit import
x: int32 = int32(42)  # tpyc: error(/int32.*requires.*from tpy import/)
