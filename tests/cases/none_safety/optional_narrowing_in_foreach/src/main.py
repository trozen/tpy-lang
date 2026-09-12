# Optional narrowing proven before a for-each loop is preserved inside the body.
from tpy import int32

def sum_items(items: list[int32], bonus: int32 | None) -> int32:
    total: int32 = 0
    if bonus is not None:
        # bonus narrowed to int32 here
        for item in items:
            total = total + item + bonus
        return total
    for item in items:
        total = total + item
    return total

print(sum_items([1, 2, 3], 10))
print(sum_items([1, 2, 3], None))

def assert_then_loop(x: int32 | None, items: list[int32]) -> int32:
    assert x is not None
    total: int32 = 0
    for item in items:
        total = total + item + x
    return total

print(assert_then_loop(5, [1, 2, 3]))
