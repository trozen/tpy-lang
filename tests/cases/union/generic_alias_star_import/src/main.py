# Generic alias imported via `from lib import *`. Star-import has a
# separate code path in sema for propagating TypeAliasInfo metadata
# (needed for the isinstance diagnostic to fire correctly on the
# imported generic alias).
from tpy import Int32
from lib import *


def main() -> None:
    p: Pair[Int32] = (Int32(1), Int32(2))  # tpyc: type(/tuple\[Int32, Int32\]/)
    print(p)


main()
