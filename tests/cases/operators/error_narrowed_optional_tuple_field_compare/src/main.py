# A pair of NARROWED `Optional[tuple]` fields compared to each other: the
# narrowed read unwraps, so it is not the bare member pair the row admits.
# Concretely, `a.maybe == b.maybe` after both are narrowed non-None; TPy
# rejects comparing the two narrowed optional-tuple fields directly.
from dataclasses import dataclass
from tpy import int32


@dataclass
class Point:
    x: int32 = 0


class N:
    maybe: tuple[Point, int32] | None

    def __init__(self, p: tuple[Point, int32]) -> None:
        self.maybe = p


def eq(a: N, b: N) -> bool:
    if a.maybe is not None and b.maybe is not None:
        # Both operands are narrowed optional field reads.
        return a.maybe == b.maybe  # tpyc: error(/in\ function\ 'eq':\ this\ construct\ is/)
    return False


def main() -> None:
    pt = Point(1)
    print(eq(N((pt, 2)), N((pt, 2))))


main()
