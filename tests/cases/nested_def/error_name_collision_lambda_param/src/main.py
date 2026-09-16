# A nested def that shadows a module function and carries a LAMBDA PARAM of
# the same name. The param binds only inside the lambda, so the read outside it
# is still the nested def's own name and meets the recursion diagnostic -- the
# "no recursive nested defs" restriction under Lambda / Closures in
# docs/LANGUAGE_FEATURES.md, which a sub-scope binding of the same name must
# not switch off.
from typing import Callable

from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    def helper(x: int32) -> int32:
        if x > 100:
            return x
        step: Callable[[int32], int32] = lambda helper: helper + 1
        # this read is OUTSIDE the lambda's scope, so it is the nested def
        return helper(x + 200) + step(0) - 1  # tpyc: error(/Recursive nested functions are not supported/)

    print(helper(1))


main()
