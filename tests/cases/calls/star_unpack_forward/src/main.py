# forwarding *args to another *args function
from tpy import Int32

def sum_all(*args: Int32) -> Int32:
    total: Int32 = 0
    for x in args:
        total += x
    return total

def double_sum(*args: Int32) -> Int32:
    return sum_all(*args) * 2

def main() -> None:
    print(double_sum(1, 2, 3))
    print(double_sum(10))

main()
