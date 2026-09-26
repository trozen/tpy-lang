# Aug-assign on an unannotated float local keeps it float; a float32 local stays
# float32 when multiplied by a bare float literal (the literal adapts to context).
# An int local whose aug-assign makes it float is refused (inference/error_aug_assign_int_float).

from tpy import float32

def float_seed() -> float:
    x = 14.0       # tpyc: type(float)
    x *= 1.3
    return x

def float_seed_add() -> float:
    y = 10.0       # tpyc: type(float)
    y += 0.5
    return y

def chain() -> float:
    z = 5.0        # tpyc: type(float)
    z += 0.5
    z *= 2.0
    return z

def float_of_int_seed(n: int) -> float:
    # float(n) spells the int seed as the float CPython would compute
    x = float(n)   # tpyc: type(float)
    x *= 1.3
    return x + 1.0   # 19.2, not 19.0

def float32_stays() -> float32:
    x = float32(1.5)   # tpyc: type(float32) -- stays float32 because 2.0 is a literal that adapts
    x *= 2.0
    return x

print(float_seed())
print(float_seed_add())
print(chain())
print(float_of_int_seed(14))
print(float32_stays())
