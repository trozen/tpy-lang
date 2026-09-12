# *list unpacking at call site to *args function
from tpy import int32

def sum_all(*args: int32) -> int32:
    total: int32 = 0
    for x in args:
        total += x
    return total

def main() -> None:
    items: list[int32] = [1, 2, 3]
    print(sum_all(*items))

    more: list[int32] = [10, 20]
    print(sum_all(*more))

main()
