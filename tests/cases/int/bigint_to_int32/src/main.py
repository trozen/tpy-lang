"""Test BigInt -> int32 conversions with range checks."""
from tpy import int32


# 1. Function parameter: BigInt passed to int32 param
def takes_int32(x: int32) -> int32:
    return x


# 2. Return statement: BigInt returned as int32
def return_as_int32(x: int) -> int32:
    return x


# 3. Variable declaration: BigInt assigned to int32 var
def var_decl_test(x: int) -> int32:
    result: int32 = x
    return result


# 4. Assignment: BigInt assigned to int32 var
def assign_test(x: int) -> int32:
    result: int32 = 0
    result = x
    return result


# 5. For loop with BigInt bound
def loop_test(n: int) -> int32:
    total: int32 = 0
    for i in range(n):
        total += 1
    return total


# 6. int32() constructor from BigInt
def constructor_test(x: int) -> int32:
    return int32(x)


# 7. Literal arithmetic: BigInt result assigned to int32
a: int32 = 1 + 2           # addition
b: int32 = 10 - 3          # subtraction
c: int32 = 4 * 5           # multiplication
d: int32 = 17 // 3         # division
e: int32 = 2 ** 10         # power


def literal_ops_local() -> int32:
    """Local variable with literal arithmetic."""
    x: int32 = 100 + 200
    return x


# Test all conversions
n = 5

# Function param
result1 = takes_int32(n)
print(result1)

# Return as int32
result2 = return_as_int32(10)
print(result2)

# Variable declaration
result3 = var_decl_test(15)
print(result3)

# Assignment
result4 = assign_test(20)
print(result4)

# For loop
result5 = loop_test(3)
print(result5)

# int32() constructor
result6 = constructor_test(25)
print(result6)

# Literal arithmetic (global)
print(a)  # 3
print(b)  # 7
print(c)  # 20
print(d)  # 5
print(e)  # 1024

# Literal arithmetic (local)
print(literal_ops_local())  # 300
