# map() builtin with named functions and lambdas
from tpy import int32

def double(x: int32) -> int32:
    return x * 2

def to_str(x: int32) -> str:
    return "v:" + str(x)

def main() -> None:
    nums = [1, 2, 3, 4, 5]

    # named function
    for x in map(double, nums):
        print(x)

    # lambda
    for x in map(lambda x: x + 10, nums):
        print(x)

    # type-changing map (int32 -> str)
    for s in map(to_str, nums):
        print(s)

    # map over empty list
    empty: list[int32] = []
    for x in map(double, empty):
        print(x)

    # chained: map + list constructor
    result = list(map(lambda x: x * x, nums))
    print(result)

main()
