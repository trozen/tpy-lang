# A nested def that shadows a module function and carries a LAMBDA PARAM of
# the same name. The param binds only inside the lambda, so the nested body
# does not own the name and the read outside the lambda still resolves to the
# module function (CPython reaches the nested def and prints 201) -- the shadow
# gate must stay on. The reject tag names the first landmark construct inside
# the rejected statement, hence `expr.lambda`
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from typing import Callable

from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    # the `helper(x + 200)` read is OUTSIDE the lambda's scope; the reject is
    # on the def
    def helper(x: int32) -> int32:  # tpyc: error(/expr\.lambda/)
        if x > 100:
            return x
        step: Callable[[int32], int32] = lambda helper: helper + 1
        return helper(x + 200) + step(0) - 1

    print(helper(1))


main()
