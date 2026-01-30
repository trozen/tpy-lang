"""Tests that list literals can be coerced to Array types.

List *literals* have compile-time known size, so they can be assigned
to Array[T, N] variables. List *variables* cannot (see errors/list_var_to_array).

Note: Passing list literals directly to Array parameters and list repeat
expressions require additional codegen support not yet implemented.
"""
from tpy import Int32, Array


def sum_array(arr: Array[Int32, 3]) -> Int32:
    return arr[0] + arr[1] + arr[2]


def main():
    # List literal assigned to Array variable
    arr1: Array[Int32, 3] = [1, 2, 3]
    print(sum_array(arr1))  # 6

    # Another example with different values
    arr2: Array[Int32, 3] = [10, 20, 30]
    print(sum_array(arr2))  # 60


main()
