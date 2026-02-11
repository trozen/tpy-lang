from tpy import Int32

x: Int32 = 1
msg: str = "x must be positive"
assert x > 0, msg  # tpyc: error(/assert message must be a string literal/)
