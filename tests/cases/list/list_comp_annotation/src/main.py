# List comprehension: annotation propagation (element type coercion)
from tpy import int32, int64, Own

def make_bigints() -> Own[list[int]]:
    items: list[int32] = [1, 2, 3]
    return [x for x in items]

def accept_wide(items: list[int64]) -> None:
    print(items)

def main() -> None:
    # Widen int32 -> BigInt via annotation
    items: list[int32] = [10, 20, 30]
    big: list[int] = [x for x in items]
    print(big)

    # Widen int32 -> int64
    wide: list[int64] = [x for x in items]
    print(wide)

    # Return type propagation (Own[list[int]] from int32 source)
    result = make_bigints()
    print(result)

    # Annotation with expression (coercion applies to element expr result)
    doubled: list[int] = [x * 2 for x in items]
    print(doubled)

    # Annotation + filter: coercion inside conditional push_back
    big_pos: list[int] = [x for x in items if x > 15]
    print(big_pos)

    # Comprehension as function argument (temp variable for rvalue binding)
    accept_wide([x for x in items])

    # No annotation: element type inferred from iterable (no coercion)
    same = [x + 1 for x in items]
    print(same)

main()
