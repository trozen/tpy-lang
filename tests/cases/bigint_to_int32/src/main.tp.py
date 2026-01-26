"""Test BigInt -> Int32 conversions with range checks."""
from tpy import Int32


# 1. Function parameter: BigInt passed to Int32 param
def takes_int32(x: Int32) -> Int32:
    return x


# 2. Return statement: BigInt returned as Int32
def return_as_int32(x: int) -> Int32:
    return x


# 3. Variable declaration: BigInt assigned to Int32 var
def var_decl_test(x: int) -> Int32:
    result: Int32 = x
    return result


# 4. Assignment: BigInt assigned to Int32 var
def assign_test(x: int) -> Int32:
    result: Int32 = 0
    result = x
    return result


# 5. For loop with BigInt bound
def loop_test(n: int) -> Int32:
    total: Int32 = 0
    for i in range(n):
        total += 1
    return total


# 6. Int32() constructor from BigInt
def constructor_test(x: int) -> Int32:
    return Int32(x)


# Test all conversions
n = 5

# Function param
result1 = takes_int32(n)
print(result1)

# Return as Int32
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

# Int32() constructor
result6 = constructor_test(25)
print(result6)
