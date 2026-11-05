# An omitting call no @overload copy takes: the implementation's default 200
# is out of the only omitting copy's int8 range, so that copy declares none
# (the int32 copy needs `y`). A `Final` or fixed-int constructor default out
# of range still wraps (BUGS.md#fixed-int-default-final-out-of-range).
from typing import overload
from tpy import int8, int32


@overload
def f(x: int8 = 0) -> str: ...
@overload
def f(x: int32, y: str) -> str: ...
def f(x: int8 | int32 = 200, y: str = "") -> str:
    return "f"


def main() -> None:
    # the call omits `x`, which no copy can take
    print(f())  # tpyc: error(/No matching overload for 'f' with argument types \(\)/)


main()
