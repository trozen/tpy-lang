# Setters and scalar-returning helpers must mutate the original record through
# forwarded, conditional, repeated, and readonly-observing aliases.
from tpy import int32, nocopy


@nocopy
class Cell:
    value: int32
    other: int32
    flag: bool

    def __init__(self, value: int32):
        self.value = value
        self.other = 0
        self.flag = False

    def update(self, value: int32):
        # A method caller must pass its receiver by reference to the free setter.
        setter(self, value)  # tpyc: ok


class Runner:
    value: int32

    def __init__(self, value: int32):
        self.value = 0
        cell = Cell(0)
        alias = cell
        # The constructor must observe its helper's write through the alias.
        setter(cell, value)  # tpyc: ok
        self.value = alias.value


def setter(cell: Cell, value: int32):
    cell.value = value


def assign(cell: Cell, value: int32) -> int32:
    setter(cell, value)
    return cell.value


def selected(left: Cell, right: Cell, flag: bool):
    alias = left
    if flag:
        alias = right
    # Only the selected origin changes, even though either may be selected.
    setter(alias, 7)  # tpyc: ok


def both(left: Cell, right: Cell):
    left.value = 8
    right.other = 9


def observe(writer: Cell, reader: Cell) -> int32:
    setter(writer, 10)
    # An inferred-readonly parameter can observe writes through another alias.
    return reader.value


def lazy(flag: bool, cell: Cell, other: Cell) -> int32:
    # The flag must select which receiver changes, without evaluating both calls.
    return assign(cell, 11) if flag else assign(other, 12)  # tpyc: ok


def main():
    cell = Cell(1)
    alias = cell
    # Scalar results and discarded void results both preserve shared mutation.
    result = assign(cell, 3)  # tpyc: ok
    print("forward", result, alias.value)
    setter(cell, 4)  # tpyc: ok
    print("void", alias.value)

    other = Cell(2)
    selected(cell, other, False)
    print("selected-left", cell.value, other.value)
    selected(cell, other, True)
    print("selected-right", cell.value, other.value)

    # Both formal parameters refer to the same actual storage.
    both(cell, cell)  # tpyc: ok
    print("repeated", alias.value, alias.other)
    print("readonly-alias", observe(cell, cell), alias.value)

    # Lazy calls change only their selected receiver; old scalar reads stay old.
    before = cell.value
    result = lazy(True, cell, other)
    print("lazy-left", before, result, alias.value, other.value)
    setter(cell, before)
    result = lazy(False, cell, other)
    print("lazy-right", result, alias.value, other.value)

    cell.update(13)
    print("method", alias.value)
    runner = Runner(14)
    print("constructor", runner.value)


main()
