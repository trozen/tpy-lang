# An @overload stub's parameter type holds in the implementation's copy for
# that stub, so a write outside it is the ordinary type error, named at the
# stub (BUGS.md#overload-written-param-outside-stub).
from typing import Literal, overload
from tpy import int32


@overload
def f(n: Literal[1]) -> int32: ...
@overload
def f(n: Literal[2]) -> int32: ...
def f(n: int32) -> int32:
    n += 1  # tpyc: error(/Augmented assignment is not supported for Literal/)
    if n == 2:
        return 100
    return 200


def main() -> None:
    print(f(1), f(2))


main()
