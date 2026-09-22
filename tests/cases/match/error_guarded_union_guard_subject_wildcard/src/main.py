# A guarded WILDCARD arm draws no `__case_N` alias, so a subject read in its
# guard would spell the raw variant -- unlike the class arm above it, whose
# alias the group scope declares ahead of the guard.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Other:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


def pick(v: Rec | None | Other) -> int32:
    match v:  # tpyc: error(/not yet supported/)
        case Rec() if v.n > 0:
            return v.n
        case _ if v is None:  # the wildcard guard reading `v` is the reject
            return -1
        case _:
            return 0


def main() -> None:
    print(pick(Rec(1)))


main()
