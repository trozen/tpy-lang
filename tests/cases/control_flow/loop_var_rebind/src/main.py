# A for-loop over an existing local rebinds it: post-loop reads see the
# last element (CPython), not a stale pre-loop value.
from tpy import int32


def scalar() -> None:
    x = 100
    for x in range(3):
        pass
    print(x)


def param_rebind(x: int32) -> None:
    for x in range(2):
        pass
    print(x)


def fresh_stays_scoped() -> None:
    total = 0
    for y in range(3):
        total = total + y
    print(total)


def main() -> None:
    scalar()
    param_rebind(50)
    fresh_stays_scoped()


main()
