from tpy import Int32

a: Int32 = 12      # 0b1100
b: Int32 = 10      # 0b1010

# Bitwise AND
print(a & b)       # 8 (0b1000)

# Bitwise OR
print(a | b)       # 14 (0b1110)

# Bitwise XOR
print(a ^ b)       # 6 (0b0110)

# Bitwise NOT
c: Int32 = 0
print(~c)          # -1

# Left shift
print(1 << 4)      # 16

# Right shift
print(32 >> 2)     # 8
