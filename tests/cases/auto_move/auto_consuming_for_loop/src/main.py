# For-loop consuming only triggers when the loop var is mutated.
# Read-only loop vars always borrow, even when container is at last use.
from tpy import int32

def sum_last_use() -> int32:
    items: list[int32] = [10, 20, 30]
    # items at last use but x is read-only -- borrows
    total: int32 = 0
    for x in items:
        total += x
    return total

def sum_not_last_use() -> int32:
    items: list[int32] = [1, 2, 3]
    total: int32 = 0
    # items is NOT at last use -- borrows
    for x in items:
        total += x
    print(len(items))  # items used after loop
    return total

def main() -> None:
    print(sum_last_use())
    print(sum_not_last_use())

main()
