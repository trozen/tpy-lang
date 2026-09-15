# A nested def that shadows a module function, read EARLIER in the enclosing
# body than the `def`. Sema resolves the earlier read through the registry and
# reaches the module `helper` (CPython raises UnboundLocalError: the `def`
# makes the name local for the whole body). The located reject keeps that
# divergence loud until sema binds the name from the start of the scope
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from tpy import int32


def helper(x: int32) -> int32:
    return x + 1


def main() -> None:
    # the read on this line is the subject; the reject is on the shadowing def
    print("before:", helper(1))

    def helper(x: int32) -> int32:  # tpyc: error(/stmt\.nested_def:nesteddef\.name_collision/)
        return x + 100

    print("after:", helper(1))


main()
