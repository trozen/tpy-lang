# Borrowed calls keep eagerly materialized constructor arguments alive through local aliases.
# @nocopy makes an accidental record copy fail; mutating the owner after the call exposes aliasing.
from tpy import int32, readonly, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

    def inspect(self, value: int32) -> int32:
        # A method binds the alias the same way as a free function.
        saved = observe(Cell(value))
        return saved.value + self.value


class Caller:
    value: int32

    def __init__(self, value: int32):
        self.value = 0
        # A constructor tail declares the argument before the reference binds.
        saved = observe(Cell(value))
        self.value = saved.value


def observe(cell: Cell) -> readonly[Cell]:
    # Named backing belongs to the caller, not to this borrow-returning callee.
    return cell


def choose(flag: bool, first: Cell, second: Cell) -> readonly[Cell]:
    return first if flag else second


def payload(value: int32) -> int32:
    print("payload", value)
    return value


def fixed():
    # Backing survives the call: BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling.
    saved = observe(Cell(payload(1)))  # tpyc: warning(/Result borrows from temporary argument/)
    print("fixed", saved.value)


def external(flag: bool):
    owner = Cell(21)
    # The owner alias must not copy: BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling.
    saved = choose(flag, owner, Cell(22))  # tpyc: warning(/Result borrows from temporary argument/)
    owner.value = 23
    print("external", saved.value)


def loops():
    remaining = 2
    while remaining > 0:
        # Backing lives through this iteration: BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling.
        saved = observe(Cell(remaining))  # tpyc: warning(/Result borrows from temporary argument/)
        print("while", saved.value)
        remaining -= 1
    for index in range(2):
        # A range iteration owns the backing: BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling.
        item = observe(Cell(index))  # tpyc: warning(/Result borrows from temporary argument/)
        print("for", item.value)


def main():
    fixed()
    external(True)
    external(False)
    cell = Cell(1)
    print("method", cell.inspect(30))
    caller = Caller(40)
    print("constructor", caller.value)
    loops()


main()
