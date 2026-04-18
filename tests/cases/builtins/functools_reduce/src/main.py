# Tests for functools.reduce()
import functools
from tpy import Int32

def add(acc: Int32, x: Int32) -> Int32:
    return acc + x

def mul(acc: Int32, x: Int32) -> Int32:
    return acc * x

def main() -> None:
    nums: list[Int32] = [1, 2, 3, 4, 5]

    # Sum via reduce
    total = functools.reduce(add, nums, Int32(0))
    print(total)  # 15

    # Product via reduce
    product = functools.reduce(mul, nums, Int32(1))
    print(product)  # 120

    # reduce with a single-element list
    single: list[Int32] = [42]
    print(functools.reduce(add, single, Int32(0)))  # 42

    # reduce with empty list returns initial
    empty: list[Int32] = []
    print(functools.reduce(add, empty, Int32(99)))  # 99

main()
