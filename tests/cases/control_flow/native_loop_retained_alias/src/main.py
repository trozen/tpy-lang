# Saving an element inside a loop keeps that element after the iterator advances.
# Mutations through the saved alias must reach the original list or Array slot.
from tpy import Array, int32, readonly


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def retained(xs: list[Cell], seed: Cell) -> int32:
    saved = seed
    source = xs
    for cell in source:
        if cell.value == 1:
            saved = cell  # tpyc: ok
        cell.value = 7
    # Advancing to the second element must not retarget saved.
    saved.value = 11  # tpyc: ok
    return saved.value


class Runner:
    result: int32

    def __init__(self, xs: readonly[list[Cell]]):
        self.result = 0
        # Constructor tail: readonly iteration still aliases its elements.
        for cell in xs:  # tpyc: ok
            self.result = cell.value

    def change(self, xs: Array[Cell, 2], seed: Cell) -> int32:
        saved = seed
        for cell in xs:
            if cell.value == 1:
                saved = cell  # tpyc: ok
            cell.value = 8
        # Method/Array twin: mutate the retained first slot after exhaustion.
        saved.value = 13  # tpyc: ok
        return saved.value


def main() -> None:
    xs = [Cell(1), Cell(2)]
    seed = Cell(3)
    print("free", retained(xs, seed), xs[0].value, xs[1].value, seed.value)
    runner = Runner(xs)
    print("constructor", runner.result)
    cells: Array[Cell, 2] = [Cell(1), Cell(2)]
    print("method", runner.change(cells, seed), cells[0].value, cells[1].value, seed.value)


main()
