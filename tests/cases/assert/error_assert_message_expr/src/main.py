from tpy import int32

x: int32 = 1
assert x > 0, 42  # tpyc: error(/assert message must be a string/)
