"""List variables cannot be coerced to Array types.

Only list *literals* and *list repeat expressions* can be coerced to Array,
because they have compile-time known size. List variables are std::vector
at runtime and cannot be converted to std::array.
"""
from tpy import int32, Array

def takes_array(arr: Array[int32, 3]) -> int32:
    return arr[0]

def main():
    nums: list[int32] = [1, 2, 3]
    takes_array(nums)  # tpyc: error(/Type mismatch/)

main()
