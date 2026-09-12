# Error: one name is either a @overload set or a @dispatch set, never both.
from typing import overload
from tpy import dispatch, int32


@overload
def f(x: int32) -> int32: ...


@dispatch
def f(x: str) -> int32:  # tpyc: error(/'f' mixes @overload and @dispatch/)
    return len(x)


def f(x: int32 | str) -> int32:
    return 0


def main() -> None:
    print(f(1))


main()
