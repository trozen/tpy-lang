# Generic function with default parameter values
from tpy import Int32

def first_or[T](items: list[T], fallback: T = Int32(0)) -> T:
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
    nums: list[Int32] = [Int32(10), Int32(20)]
    empty: list[Int32] = []

    # Call with default (fallback omitted)
    print(first_or(nums))
    print(first_or(empty))

    # Call with explicit fallback
    print(first_or(nums, Int32(99)))
    print(first_or(empty, Int32(99)))

    # Bare literal default with generic type
    print(fallback_or(nums))
    print(fallback_or(empty))

    # Generic with bool default
    print(pick(Int32(1), Int32(2)))
    print(pick(Int32(1), Int32(2), False))

main()
