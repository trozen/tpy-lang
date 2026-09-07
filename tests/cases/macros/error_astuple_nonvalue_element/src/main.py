# `dataclasses.astuple` over a dataclass with a REFERENCE-typed field: the
# expansion is a pointer-repr tuple whose form the position decides, so it
# cannot be spelled from the dataclass type alone.
import dataclasses
from tpy import Int32


class Rec:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


@dataclasses.dataclass
class Holder:
    r: Rec
    k: Int32


def main() -> None:
    h = Holder(Rec(5), 6)
    # The expanded tuple carries a reference-typed element.
    u = dataclasses.astuple(h)  # tpyc: error(/stmt\.var_decl:decl\.slot_type/)
    print(u[1])


main()
