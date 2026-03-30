# map over rvalue iterable (owning iterator prevents dangling)
from tpy import Int32, Own

def make_nums() -> Own[list[Int32]]:
    return [10, 20, 30]

def double(x: Int32) -> Int32:
    return x * 2

def main() -> None:
    # rvalue: map over a temporary returned by function
    for x in map(double, make_nums()):
        print(x)

    # rvalue: map with lambda
    result = list(map(lambda x: x + 1, make_nums()))
    print(result)

main()
