# Error: a @dispatch set has no trailing implementation; each variant is
# its own implementation.
from tpy import dispatch, Int32


@dispatch
def f(x: Int32) -> Int32:
    return x + 1


@dispatch
def f(x: str) -> Int32:
    return len(x)


def f(x: Int32 | str) -> Int32:  # tpyc: error(/@dispatch variants of 'f' cannot be followed by a trailing implementation/)
    return 0


def main() -> None:
    print(f(1))


main()
