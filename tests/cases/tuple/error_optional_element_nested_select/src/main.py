# A select nested in a select's arm over an `Own[Cell] | None` tuple element
# keeps the `Own` marker and is refused as the owned name bind is
# (BUGS.md#own-optional-target-alias-rejects); the one-level select binds
# `Cell*` (tuple/optional_element_reads, `own_ternary`).
from tpy import int32, Own


class Cell:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


def make() -> tuple[Own[Cell] | None, int32]:
    return Cell(8), 1


def check(first: bool, second: bool) -> None:
    t = make()
    p = (t[0] if second else None) if first else None  # tpyc: error(/decl.slot_type/)
    if p is not None:
        print(p.value)


check(True, True)
