# int/BigInt variable widens to float when used with aug-assign and a float operand
# (equivalent to x = x * 1.3 in Python, which widens x to float)

def bigint_widen() -> float:
    x = 14         # tpyc: type(float)   -- widens to float because x *= 1.3 follows
    x *= 1.3
    return x

def int32_literal_widen() -> float:
    y = 10         # tpyc: type(float)   -- widens to float because y += 0.5 follows
    y += 0.5
    return y

def chain_widen() -> float:
    z = 5          # tpyc: type(float)   -- widens to float because z += 0.5 follows
    z += 0.5
    z *= 2.0
    return z

def use_after_widen() -> float:
    # Subsequent use of x must see the widened float type, not the original int
    x = 14
    x *= 1.3
    return x + 1.0   # should be 19.2, not 19.0

print(bigint_widen())
print(int32_literal_widen())
print(chain_widen())
print(use_after_widen())
