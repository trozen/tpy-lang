# A container-returning call rvalue inserted into a list of lists -- the
# free-call face beside the method-call one, including the literal-seeded
# local whose element type resolves to the callee's own Own[...] spelling.
from tpy import Own


class Maker:
    k: float

    def __init__(self, k: float) -> None:
        self.k = k

    def create_vector(self) -> Own[list[float]]:
        return [self.k, self.k * 2.0]


def make_row(n: float) -> Own[list[float]]:
    return [n, n + 1.0]


MAKERS: list[Maker] = []


def build() -> Own[list[list[float]]]:
    # `table` is literal-seeded, so its element resolves to `Own[list[float]]`
    # and the append slot arrives doubly Own-wrapped.
    table = []
    for m in MAKERS:
        table.append(m.create_vector())  # tpyc: ok
    return table


def main() -> None:
    MAKERS.append(Maker(3.0))
    MAKERS.append(Maker(5.0))
    rows = build()
    print(len(rows), rows[0][0], rows[1][1])
    annotated: list[list[float]] = []
    annotated.append(make_row(1.0))  # tpyc: ok
    annotated.append(make_row(10.0))  # tpyc: ok
    # The inserted rows are the list's own storage: mutating one through the
    # container is visible on a later read.
    annotated[0].append(99.0)
    print(len(annotated), len(annotated[0]), annotated[0][2], annotated[1][0])


main()
