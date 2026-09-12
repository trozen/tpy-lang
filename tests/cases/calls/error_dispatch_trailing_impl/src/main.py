# Error: a @dispatch set has no trailing implementation; each variant is
# its own implementation.
from tpy import dispatch, int32


@dispatch
def f(x: int32) -> int32:
    return x + 1


@dispatch
def f(x: str) -> int32:
    return len(x)


def f(x: int32 | str) -> int32:  # tpyc: error(/@dispatch variants of 'f' cannot be followed by a trailing implementation/)
    return 0


def main() -> None:
    print(f(1))


main()
