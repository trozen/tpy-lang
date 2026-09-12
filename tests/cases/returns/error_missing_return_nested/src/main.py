# Missing-return enforcement applies to nested defs (shared body chokepoint).
from tpy import int32


def outer(n: int32) -> int32:
    def inner(m: int32) -> int32:  # tpyc: error(/'inner' can reach the end of the function without returning/)
        if m > 0:
            return m

    return inner(n)


def main() -> None:
    print(outer(1))


main()
