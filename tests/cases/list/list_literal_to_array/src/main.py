"""Tests that list literals can be coerced to Array types.

List *literals* have compile-time known size, so they can be:
- Assigned to Array[T, N] variables
- Passed directly to Array[T, N] parameters
- Used in initializers and conditions

List *variables* cannot be coerced to Array (see errors/list_var_to_array).
"""
from tpy import int32, Array


def sum_array(arr: Array[int32, 3]) -> int32:
    return arr[0] + arr[1] + arr[2]


def main():
    # List literal assigned to Array variable
    arr1: Array[int32, 3] = [1, 2, 3]
    print(sum_array(arr1))  # 6

    # List literal passed directly to Array parameter
    print(sum_array([10, 20, 30]))  # 60

    # List literal in variable initializer
    result: int32 = sum_array([100, 200, 300])
    print(result)  # 600

    # List literal in condition
    if sum_array([1, 1, 1]) > 0:
        print(1)  # 1
    else:
        print(0)

    # List literal in while condition (edge case)
    count: int32 = 0
    while sum_array([1, 0, 0]) > count:
        count = count + 1
    print(count)  # 1


main()
