# Multiple elif conditions each producing a temp (list literal passed by
# mutable ref): temp numbers stay sequential across the nested emits.
from tpy import int32


def take(items: list[int32]) -> int32:
    items.append(1)
    return len(items)


def test(x: int32) -> int32:
    if x < 0:
        return -1
    elif take([10]) == x:
        return 0
    elif take([20, 30]) == x:
        return 1
    else:
        return 2


def main() -> None:
    print(test(-5))
    print(test(2))
    print(test(3))
    print(test(7))


main()
