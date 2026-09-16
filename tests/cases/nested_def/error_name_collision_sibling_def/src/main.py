# A nested def that shadows a module function, called from a SIBLING nested def
# declared EARLIER in the same body. Python binds the shadowing name for the
# whole enclosing scope, so the sibling's call is the nested `helper` -- but a
# C++ lambda can only name one declared above it, so sema reports the read
# instead of reaching the module function. A sibling declared AFTER the `def`
# resolves and keeps compiling (the `sibling_after` section of
# `name_collides_with_function`). The rule is the "a sibling nested def
# declared ABOVE the shadowing one cannot call it" sentence under Lambda /
# Closures in docs/LANGUAGE_FEATURES.md.
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    def caller(y: int32) -> int32:
        # `helper` is main's local, bound below -- not the module function
        return helper(y)  # tpyc: error(/is read before the nested function 'helper'/)

    def helper(x: int32) -> int32:
        return x + 100

    print(caller(1))


main()
