from tpy import Int32
# Float value exceeds Int32 range
x: Int32 = Int32(3000000000.0)
print(x)
