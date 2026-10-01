# An `or` whose left operand is not a plain name holds it in a temporary
# declared before the enclosing statement; a lambda body has no statement of
# its own, so the shape is rejected instead of hoisting the temporary outside
# the lambda (BUGS.md#lambda-body-operand-temp-hoisted-outside).
from typing import Callable
from tpy import int32


def pick(x: int32) -> int32:
    return x * 2


def apply(f: Callable[[int32], int32], a: int32) -> int32:
    return f(a)


def main() -> None:
    # The left operand `pick(a)` reads the lambda's own parameter.
    print(apply(lambda a: pick(a) or pick(1), 3))  # tpyc: error(/expr\.lambda/)


main()
