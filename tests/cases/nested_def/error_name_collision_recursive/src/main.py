# A nested def that shadows a module function AND calls itself. Python binds
# the nested name for the whole enclosing scope, so the self-reference is the
# nested `fact`; sema now sees that binding from the scope's start and the
# call meets the recursion diagnostic instead of silently reaching the module
# `fact`. The rule is the "no recursive nested defs" restriction under Lambda /
# Closures in docs/LANGUAGE_FEATURES.md.
from tpy import int32


def fact(n: int32) -> int32:
    return 4012


def main() -> None:
    def fact(n: int32) -> int32:
        if n <= 1:
            return 1
        # the self-reference is the nested def, not the module function
        return n * fact(n - 1)  # tpyc: error(/Recursive nested functions are not supported/)

    print(fact(4))


main()
