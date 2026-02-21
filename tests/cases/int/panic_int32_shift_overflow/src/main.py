from tpy import Int32

x: Int32 = 1
print(x << 100)  # Should panic: shift count too large
