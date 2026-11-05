# An omitting call no @overload copy takes: the implementation's default "r"
# fits neither stub's type, so neither copy declares it
# (BUGS.md#overload-stub-default-type-mismatch-refused).
from typing import Literal, overload
from tpy import int32


@overload
def f(x: int32) -> str: ...
@overload
def f(x: Literal["rb"] = "rb") -> str: ...
def f(x: int32 | str = "r") -> str:
    return "f"


def main() -> None:
    # the call omits `x`, which no copy can take
    print(f())  # tpyc: error(/'f' expects 1 argument\(s\), got 0/)


main()
