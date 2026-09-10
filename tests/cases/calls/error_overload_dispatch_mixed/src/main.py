# Error: one name is either a @overload set or a @dispatch set, never both.
from typing import overload
from tpy import dispatch, Int32


@overload
def f(x: Int32) -> Int32: ...


@dispatch
def f(x: str) -> Int32:  # tpyc: error(/'f' mixes @overload and @dispatch/)
    return len(x)


def f(x: Int32 | str) -> Int32:
    return 0


def main() -> None:
    print(f(1))


main()
