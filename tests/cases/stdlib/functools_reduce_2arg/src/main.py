# functools.reduce 2-arg form (uses a[0] as seed). Restored after the
# per-candidate Fn arg typing fix unblocked the multi-overload reduce
# resolution.
from functools import reduce
from tpy import int32


def add(a: int32, b: int32) -> int32:
    return a + b


def main() -> None:
    xs: list[int32] = [1, 2, 3, 4, 5]

    # Named function
    print(reduce(add, xs))                       # 15

    # Lambda (max via comparison)
    print(reduce(lambda a, b: max(a, b), xs))    # 5

    # Lambda (multiplication)
    print(reduce(lambda a, b: a * b, xs))        # 120

    # Single-element list -- returns that element directly.
    one: list[int32] = [42]
    print(reduce(add, one))                      # 42


main()
