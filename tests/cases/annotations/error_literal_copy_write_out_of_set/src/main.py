# Inside a Literal[2] copy over a BigInt parameter, a write outside the set
# is refused at the stub's type (BUGS.md#overload-written-param-outside-stub).
from typing import Literal, overload


@overload
def f(x: Literal[2]) -> int: ...
@overload
def f(x: int) -> int: ...
def f(x: int) -> int:
    x = 3  # tpyc: error(/expected Literal\[2\], got 3/)
    if x == 2:
        return 100
    return x


print(f(2))
