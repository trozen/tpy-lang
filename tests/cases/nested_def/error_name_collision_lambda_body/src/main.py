# A nested def that shadows a module function and reads the shadowed name from
# a LAMBDA inside its own body. A lambda body is its own scope in the AST but
# not for name binding: `helper` there is the enclosing scope's local, the
# nested def itself, so the read meets the recursion diagnostic instead of
# reaching the module `helper` -- the "no recursive nested defs" restriction
# under Lambda / Closures in docs/LANGUAGE_FEATURES.md.
from typing import Callable

from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    def helper(x: int32) -> int32:
        if x > 100:
            return x
        # the lambda body shares the nested def's binding for `helper`
        step: Callable[[int32], int32] = lambda k: helper(k + 200)  # tpyc: error(/Recursive nested functions are not supported/)
        return step(x)

    print(helper(1))


main()
