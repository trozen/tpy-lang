# A loop var iterated out of a SUBSCRIPT of another loop var roots back at the
# storage the outer loop came from -- so a `readonly` root keeps the whole
# chain readonly and the write through the element is rejected, rather than
# quietly crediting the parameter with a mutation it may not have.
from tpy import int32, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Grid:
    nested: list[list[list[Box]]]

    def __init__(self) -> None:
        self.nested = [[[Box(1)]]]


def f(g: readonly[Grid]) -> None:
    for rows in g.nested:
        for b in rows[0]:
            b.n += 5  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    pass


main()
