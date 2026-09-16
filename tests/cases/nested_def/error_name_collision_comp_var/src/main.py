# A nested def that shadows a module function and reuses the same name as a
# COMPREHENSION variable. The comprehension is its own scope, so the read
# outside it is still the nested def's own name and meets the recursion
# diagnostic -- the "no recursive nested defs" restriction under Lambda /
# Closures in docs/LANGUAGE_FEATURES.md, which a sub-scope binding of the same
# name does not switch off.
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    xs = [1, 2]

    def helper(x: int32) -> int32:
        if x > 100:
            return x
        seen = [helper for helper in xs]
        # this read is OUTSIDE the comprehension, so it is the nested def
        return helper(x + 200) + seen[0] - 1  # tpyc: error(/Recursive nested functions are not supported/)

    print(helper(1))


main()
