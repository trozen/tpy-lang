# The branch-first Optional hoist admits an OWNED inner (`str | None` binds
# `std::optional<std::string>`); a VIEW inner keeps rejecting, since the
# predecl would be an `optional<string_view>` aliasing whatever each arm bound.
from tpy import int32, StrView


def pick(c: bool, a: StrView, b: StrView) -> int32:
    if c:  # tpyc: error(/not yet supported/)
        s: StrView | None = a
    else:
        s = b
    if s is None:
        return 0
    return len(s)


def main() -> None:
    print(pick(True, "ab", "cde"))


main()
