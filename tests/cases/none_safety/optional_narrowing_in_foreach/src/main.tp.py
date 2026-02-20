# Optional narrowing proven before a for-each loop is preserved inside the body.
from tpy import Int32

def sum_items(items: list[Int32], bonus: Int32 | None) -> Int32:
    total: Int32 = 0
    if bonus is not None:
        # bonus narrowed to Int32 here
        for item in items:
            total = total + item + bonus
        return total
    for item in items:
        total = total + item
    return total

print(sum_items([1, 2, 3], 10))
print(sum_items([1, 2, 3], None))

def assert_then_loop(x: Int32 | None, items: list[Int32]) -> Int32:
    assert x is not None
    total: Int32 = 0
    for item in items:
        total = total + item + x
    return total

print(assert_then_loop(5, [1, 2, 3]))
