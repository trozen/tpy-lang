# Test power operator (**)

# Basic powers
print(2 ** 0)   # 1
print(2 ** 1)   # 2
print(2 ** 10)  # 1024
print(3 ** 3)   # 27

# Large exponents (arbitrary precision)
print(2 ** 32)   # 4294967296
print(2 ** 64)   # 18446744073709551616
print(10 ** 20)  # 100000000000000000000

# Negative base with even/odd exponents
print((-2) ** 3)  # -8
print((-2) ** 4)  # 16

# Zero base
print(0 ** 5)  # 0
print(0 ** 0)  # 1 (by convention)

# Power with variables
x = 5
y = 3
print(x ** y)  # 125
