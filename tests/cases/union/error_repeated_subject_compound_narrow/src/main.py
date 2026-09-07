# An `and` chain that tests the SAME union subject twice: not lowered yet, so
# the case pins the reject.
from tpy import Int32


class Alpha:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Beta:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def probe(v: Alpha | Beta, w: Alpha | Beta) -> Int32:
    # The same subject twice inside one condition is a re-narrow with no
    # mirrored emit; the distinct-subjects gate rejects.
    if isinstance(v, Alpha) and isinstance(w, Beta) and isinstance(v, Alpha):  # tpyc: error(/if.cond_facts_unmirrored/)
        return v.x + w.y
    return 0


def main() -> None:
    print(probe(Alpha(1), Beta(2)))


main()
