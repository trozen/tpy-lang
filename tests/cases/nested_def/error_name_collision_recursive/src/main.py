# A nested def that shadows a module function AND calls itself. Sema binds the
# shadowing name only from the `def` onward, so the self-reference resolves to
# the MODULE `fact` (CPython recurses into the nested one and prints 24). The
# located reject keeps that divergence loud until sema treats the name as a
# local of the enclosing scope from its start
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from tpy import int32


def fact(n: int32) -> int32:
    return 4012


def main() -> None:
    # the self-reference on the last line is the subject; the reject is on the def
    def fact(n: int32) -> int32:  # tpyc: error(/stmt\.nested_def:nesteddef\.name_collision/)
        if n <= 1:
            return 1
        return n * fact(n - 1)

    print(fact(4))


main()
