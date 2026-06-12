# Missing-return enforcement applies to nested defs (shared body chokepoint).
from tpy import Int32


def outer(n: Int32) -> Int32:
    def inner(m: Int32) -> Int32:  # tpyc: error(/'inner' can reach the end of the function without returning/)
        if m > 0:
            return m

    return inner(n)


def main() -> None:
    print(outer(1))


main()
