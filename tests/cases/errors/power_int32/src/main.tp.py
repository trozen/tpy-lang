# Test that power with Int32 is rejected
from tpy import Int32
x: Int32 = 2
y = x ** 3  # tpyc: error(/Power operator.*Int32/)
