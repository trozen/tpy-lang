# *list unpacking at call site to *args function
from tpy import Int32

def sum_all(*args: Int32) -> Int32:
    total: Int32 = 0
    for x in args:
        total += x
    return total

def main() -> None:
    items: list[Int32] = [1, 2, 3]
    print(sum_all(*items))

    more: list[Int32] = [10, 20]
    print(sum_all(*more))

main()
