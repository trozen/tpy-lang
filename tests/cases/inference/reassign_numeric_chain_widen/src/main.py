# Numeric widening at global scope: literal-seeded -> Int32 -> BigInt -> float chain
from tpy import Int32

x = 0  # tpyc: type(int)
x = Int32(10)  # tpyc: type(int)
x = int(20)  # tpyc: type(int)
print(x)

f = 0  # tpyc: type(float)
f = Int32(3)  # tpyc: type(float)
f = 1.5  # tpyc: type(float)
print(f)
