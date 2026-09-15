# A nested def that shadows a module function and reuses the same name as a
# COMPREHENSION variable. The comprehension is its own scope, so the nested
# body does not own the name and the read outside the comprehension still
# resolves to the module function (CPython reaches the nested def and prints
# 201). The reject tag names the first landmark construct inside the rejected
# statement, hence `expr.list_comp`
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    xs = [1, 2]

    # the `helper(x + 200)` read is OUTSIDE the comprehension; the reject is
    # on the def
    def helper(x: int32) -> int32:  # tpyc: error(/expr\.list_comp/)
        if x > 100:
            return x
        seen = [helper for helper in xs]
        return helper(x + 200) + seen[0] - 1

    print(helper(1))


main()
