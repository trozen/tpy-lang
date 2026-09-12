# Phase 6: multi-level MRO walk. `Grandchild.LIMIT` (or instance-side) resolves
# to the declaring `Grand` ancestor; codegen emits `Grand::LIMIT`.
from typing import Final
from tpy import int32


class Grand:
    LIMIT: Final[int32] = 10


class Mid(Grand):
    pass


class Grandchild(Mid):
    pass


def main() -> None:
    print(Grandchild.LIMIT)
    print(Mid.LIMIT)
    print(Grand.LIMIT)
    g = Grandchild()
    print(g.LIMIT)


main()
