from tpy import int32

a: int32 = 12      # 0b1100
b: int32 = 10      # 0b1010

# Bitwise AND
print(a & b)       # 8 (0b1000)

# Bitwise OR
print(a | b)       # 14 (0b1110)

# Bitwise XOR
print(a ^ b)       # 6 (0b0110)

# Bitwise NOT
c: int32 = 0
print(~c)          # -1

# Left shift
print(1 << 4)      # 16

# Right shift
print(32 >> 2)     # 8

# Large shift (Python semantics - arbitrary precision)
print(1 << 100)    # 1267650600228229401496703205376
print((1 << 100) >> 90)  # 1024

# BigInt bitwise operators
x = 12  # BigInt
y = 10  # BigInt
print(x & y)       # 8
print(x | y)       # 14
print(x ^ y)       # 6

# BigInt bitwise NOT (Python: ~x = -(x+1))
z = 0
print(~z)          # -1
z = 5
print(~z)          # -6

# Large BigInt bitwise
big1 = (1 << 100) | (1 << 50)  # tpyc: warning(/outside default int32 range/)
big2 = (1 << 100) | (1 << 25)  # tpyc: warning(/outside default int32 range/)
print((big1 & big2) >> 100)         # 1 - only bit 100 in common
print((big1 | big2) >> 100)         # 1 - bit 100 is set
print((big1 ^ big2) >> 50)          # 1 - bit 50 differs (in big1 only)
