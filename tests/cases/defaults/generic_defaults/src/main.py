# Generic function with default parameter values
from tpy import int32

def first_or[T](items: list[T], fallback: T = int32(0)) -> T:
    if len(items) > 0:
        return items[0]
    return fallback

def fallback_or[T](items: list[T], fallback: T = 0) -> T:
    if len(items) > 0:
        return items[0]
    return fallback

def pick[T](a: T, b: T, use_first: bool = True) -> T:
    if use_first:
        return a
    return b

def main() -> None:
    nums: list[int32] = [int32(10), int32(20)]
    empty: list[int32] = []

    # Call with default (fallback omitted)
    print(first_or(nums))
    print(first_or(empty))

    # Call with explicit fallback
    print(first_or(nums, int32(99)))
    print(first_or(empty, int32(99)))

    # Bare literal default with generic type
    print(fallback_or(nums))
    print(fallback_or(empty))

    # Generic with bool default
    print(pick(int32(1), int32(2)))
    print(pick(int32(1), int32(2), False))

main()
