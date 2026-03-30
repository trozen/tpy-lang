# filter() builtin with named functions and lambdas
from tpy import Int32

def is_even(x: Int32) -> bool:
    return x % 2 == 0

def main() -> None:
    nums = [1, 2, 3, 4, 5, 6]

    # named function
    for x in filter(is_even, nums):
        print(x)

    # lambda
    for x in filter(lambda x: x > 3, nums):
        print(x)

    # filter to empty result
    for x in filter(lambda x: x > 100, nums):
        print(x)

    # filter over empty list
    empty: list[Int32] = []
    for x in filter(is_even, empty):
        print(x)

    # chained: filter + list constructor
    result = list(filter(lambda x: x % 2 != 0, nums))
    print(result)

main()
