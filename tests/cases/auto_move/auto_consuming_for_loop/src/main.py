# Auto-consuming for-loop: iterable at last use uses consuming __iter__.
# The compiler detects list at last use and calls own_iter(std::move(items))
# instead of borrowing __iter__.
from tpy import Int32

def sum_last_use() -> Int32:
    items: list[Int32] = [10, 20, 30]
    # items is at last use -- auto-consumes
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
