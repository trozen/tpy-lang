# Cross-type cast overflow: int32 value too large for uint8
from tpy import int32, uint8

x: int32 = int32(300)
y: uint8 = uint8(x)
print(y)
