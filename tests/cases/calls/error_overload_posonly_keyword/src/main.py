# A positional-only parameter passed by keyword at an overload set: no
# candidate binds the call, all for that one reason, which is reported.
from tpy import dispatch, int32


@dispatch
def f(x: int32, /) -> str:
    return "int"


@dispatch
def f(x: str, /) -> str:
    return "str"


def main() -> None:
    print(f(x=1))  # tpyc: error(/'f' parameter 'x' is positional-only and cannot be passed by keyword/)


main()
