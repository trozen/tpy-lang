from tpy import Int32

x: Int32 = 1
assert x > 0, 42  # tpyc: error(/assert message must be a string/)
