# A nested def that shadows a module function, called from a SIBLING nested def
# declared EARLIER in the same body. Python binds the shadowing name for the
# whole enclosing scope, so the sibling's call reaches the nested `helper`
# (CPython prints 101); sema binds it only from the `def` onward and the
# sibling's call reaches the module one. A sibling declared AFTER the `def`
# resolves correctly and keeps compiling -- the `sibling_after` section of
# `name_collides_with_function` pins that
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    def caller(y: int32) -> int32:
        # the call on this line is the subject; the reject is on the shadowing def
        return helper(y)

    def helper(x: int32) -> int32:  # tpyc: error(/stmt\.nested_def:nesteddef\.name_collision/)
        return x + 100

    print(caller(1))


main()
