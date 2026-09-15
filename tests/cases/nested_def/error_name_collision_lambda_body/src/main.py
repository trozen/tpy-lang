# A nested def that shadows a module function and reads the shadowed name from
# a LAMBDA inside its own body. A lambda body is its own scope in the AST
# (`TpyLambda.children()` is empty) but not for name binding: `helper` there is
# the enclosing scope's local, the nested def itself (CPython prints 201),
# while sema resolves it through the registry and reaches the module `helper`.
# The reject tag names the first landmark construct inside the rejected
# statement, so this one reads `expr.lambda` rather than the
# `nesteddef.name_collision` detail the lambda-free shapes carry
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from typing import Callable

from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    # the `helper` call inside the lambda is the subject; the reject is on the def
    def helper(x: int32) -> int32:  # tpyc: error(/expr\.lambda/)
        if x > 100:
            return x
        step: Callable[[int32], int32] = lambda k: helper(k + 200)
        return step(x)

    print(helper(1))


main()
