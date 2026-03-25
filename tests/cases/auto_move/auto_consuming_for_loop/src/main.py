# For-loop iteration always borrows (consuming disabled until per-element move).
# Verifies that last-use vs non-last-use both generate borrowing codegen.
from tpy import Int32

def sum_last_use() -> Int32:
    items: list[Int32] = [10, 20, 30]
    # items at last use -- still borrows (no per-element move yet)
    total: Int32 = 0
    for x in items:
        total += x
    return total

def sum_not_last_use() -> Int32:
    items: list[Int32] = [1, 2, 3]
    total: Int32 = 0
    # items is NOT at last use -- borrows
    for x in items:
        total += x
    print(len(items))  # items used after loop
    return total

def main() -> None:
    print(sum_last_use())
    print(sum_not_last_use())

main()
