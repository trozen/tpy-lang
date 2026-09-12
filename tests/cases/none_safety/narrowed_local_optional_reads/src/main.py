# Regression: sibling of narrowed_field_aug_assign / _print_bigint, but for
# value-Optional LOCALS and PARAMS. Storage is std::optional<T> after
# narrowing too, so the same read sites (aug-assign LHS read, BigInt print,
# subscript-LHS receiver) need the same unwrap.
from tpy import int32


def step(x: int | None, y: int32 | None, lst: list[int] | None) -> None:
    if x is None:
        return
    if y is None:
        return
    if lst is None:
        return
    x += 1
    y *= 2
    lst[0] = x + y
    # Avoid `print(lst)` here -- pointer-repr Optional list print is a
    # separate pre-existing gap; this test focuses on the narrowed-local
    # aug-assign + subscript-LHS fixes.
    print(x, y, lst[0])


def local_path() -> None:
    a: int | None = 10
    b: int32 | None = 3
    if a is None:
        return
    if b is None:
        return
    a -= 4
    b += 5
    print(a, b)


def main() -> None:
    step(7, 8, [0, 0, 0])
    local_path()


main()
