# Numeric widening at global scope: literal-seeded -> int32 -> BigInt chain, and a
# float chain (float -> float32 value -> float literal) that stays float
from tpy import int32, float32

x = 0  # tpyc: type(int)
x = int32(10)  # tpyc: type(int)
x = int(20)  # tpyc: type(int)
print(x)

f = 0.0  # tpyc: type(float)
f = float32(3.0)  # tpyc: type(float)
f = 1.5  # tpyc: type(float)
print(f)
