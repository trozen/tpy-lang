# forwarding *args to another *args function
from tpy import int32

def sum_all(*args: int32) -> int32:
    total: int32 = 0
    for x in args:
        total += x
    return total

def double_sum(*args: int32) -> int32:
    return sum_all(*args) * 2

def main() -> None:
    print(double_sum(1, 2, 3))
    print(double_sum(10))

main()
