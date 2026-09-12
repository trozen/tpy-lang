from tpy import int32

x: int32 = 1
print(x << 100)  # Should panic: shift count too large
