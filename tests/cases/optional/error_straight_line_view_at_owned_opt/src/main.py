# A `str` PARAM (a view) bound straight-line into an owned-inner
# `Optional[str]` slot. `std::optional<std::string>`'s converting ctor from
# `std::string_view` is EXPLICIT, so the bare `= s` copy-init does not compile,
# and the decl has no arm for the view->owned wrap the return sink already
# spells. The BRANCH-FIRST twin of the same declaration compiles (the hoist
# predecls the slot and the branch write is an ASSIGN, whose operator= takes
# the view), so one construct currently has two verdicts by position --
# BUGS.md#view-at-owned-opt-decl-by-position.
from tpy import int32


def straight_line(s: str) -> int32:
    t: str | None = s  # tpyc: error(/not yet supported/)
    if t is None:
        return -1
    return len(t)


def main() -> None:
    print(straight_line("abcd"))


main()
