# Generic alias imported via `from lib import *`. Star-import has a
# separate code path in sema for propagating TypeAliasInfo metadata
# (needed for the isinstance diagnostic to fire correctly on the
# imported generic alias).
from tpy import int32
from lib import *


def main() -> None:
    p: Pair[int32] = (int32(1), int32(2))  # tpyc: type(/tuple\[int32, int32\]/)
    print(p)


main()
