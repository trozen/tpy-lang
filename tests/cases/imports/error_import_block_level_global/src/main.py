# A module-level binding made INSIDE a block body has no module slot (see
# BUGS.md#block-level-module-binding-has-no-slot), so importing it must be
# rejected by name rather than passing sema and failing in the C++ compiler.
from tpy import int32
from lib import WIDTH, LOOPY  # tpyc: error(/'LOOPY' not found in module 'lib'/)


def main() -> int32:
    return WIDTH + LOOPY


main()
