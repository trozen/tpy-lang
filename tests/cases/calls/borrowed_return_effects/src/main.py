# Returned records must alias the selected argument through forwarding and reseats.
# Mutations in both directions expose copies; readonly aliases observe owner writes.
from tpy import int32, nocopy, readonly


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

    def via_method(self) -> int32:
        # A method caller must retain the free callee's alias of its receiver.
        saved = identity(self)  # tpyc: ok
        saved.value = 51
        return self.value


def identity(cell: Cell) -> Cell:
    return cell


def choose(flag: bool, first: Cell, second: Cell) -> Cell:
    return first if flag else second


def forward(flag: bool, first: Cell, second: Cell) -> Cell:
    # Reordered arguments prevent forwarding by parameter index without remapping.
    return choose(flag, second, first)  # tpyc: ok


def observe(cell: readonly[Cell]) -> readonly[Cell]:
    return cell


class Caller:
    value: int32

    def __init__(self, value: int32):
        self.value = 0
        local = Cell(value)
        # Constructor-tail callers use the same borrowed result as free callers.
        saved = identity(local)  # tpyc: ok
        saved.value = 61
        self.value = local.value


def selected(flag: bool):
    first = Cell(1)
    second = Cell(2)
    # Each flag selects a different original; copying cannot pass @nocopy.
    saved = forward(flag, first, second)  # tpyc: ok
    first.value = 11
    second.value = 22
    print("selected", flag, saved.value)
    saved.value = 33
    print("write-back", flag, first.value, second.value)


def reseated():
    first = Cell(1)
    second = Cell(2)
    holder = first
    saved = identity(holder)  # tpyc: ok
    # Changing the intermediate holder must not change the already returned alias.
    holder = identity(second)  # tpyc: ok
    saved = identity(saved)  # tpyc: ok
    first.value = 41
    second.value = 42
    print("reseated", saved.value, holder.value)


def repeated():
    cell = Cell(1)
    # Two argument positions may denote the same storage.
    saved = forward(False, cell, cell)  # tpyc: ok
    saved.value = 43
    print("repeated", cell.value)


def readonly_result():
    cell = Cell(1)
    # Readonly restricts writes through this alias; it must still see owner writes.
    saved = observe(cell)  # tpyc: ok
    cell.value = 44
    print("readonly", saved.value)


def lazy(flag: bool):
    first = Cell(1)
    second = Cell(2)
    # Selection retains the chosen call's alias, rather than materializing a value.
    saved = identity(first) if flag else identity(second)  # tpyc: ok
    first.value = 71
    second.value = 72
    print("lazy", flag, saved.value)


def main():
    selected(True)
    selected(False)
    reseated()
    repeated()
    readonly_result()
    lazy(True)
    lazy(False)
    cell = Cell(1)
    print("method", cell.via_method())
    print("constructor", Caller(1).value)


main()
