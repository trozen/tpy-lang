# The borrow-return face of BUGS.md#block-level-module-binding-has-no-slot: a
# module-level name first bound inside a top-level block body has no module
# slot, so a function borrowing from it is rejected by name rather than
# passing sema and failing in the C++ compiler.
from tpy import StrView

for w in ["alpha", "beta"]:
    LAST = w


def view() -> StrView:
    return LAST  # tpyc: error(/Undefined variable: 'LAST'/)


def main() -> None:
    print(view())


main()
