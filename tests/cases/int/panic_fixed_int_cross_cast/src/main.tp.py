# Cross-type cast overflow: Int32 value too large for UInt8
from tpy import Int32, UInt8

x: Int32 = Int32(300)
y: UInt8 = UInt8(x)
print(y)
