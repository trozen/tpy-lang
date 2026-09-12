from tpy import int32
# Float value exceeds int32 range
x: int32 = int32(3000000000.0)
print(x)
