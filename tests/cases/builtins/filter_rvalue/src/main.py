# filter over rvalue iterable (owning iterator prevents dangling)
from tpy import Int32, Own

def make_nums() -> Own[list[Int32]]:
    return [1, 2, 3, 4, 5, 6]

def is_even(x: Int32) -> bool:
    return x % 2 == 0

def main() -> None:
    # rvalue: filter over a temporary
    for x in filter(is_even, make_nums()):
        print(x)

    # rvalue: filter with lambda, collect to list
    result = list(filter(lambda x: x > 3, make_nums()))
    print(result)

main()
