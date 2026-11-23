# A borrowed call's result aliases the argument it hands back. A fresh argument
# lives only for its statement (its kept borrow is warned:
# calls/error_fresh_arg_borrow_kept), so each argument the result keeps is
# bound to a local first. @nocopy makes an accidental record copy fail;
# mutating the owner after the call exposes aliasing.
from tpy import int32, readonly, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

    def inspect(self, value: int32) -> int32:
        # A method binds the alias the same way as a free function.
        c = Cell(value)
        saved = observe(c)
        return saved.value + self.value


class Caller:
    value: int32

    def __init__(self, value: int32):
        self.value = 0
        # A constructor tail binds it the same way.
        c = Cell(value)
        saved = observe(c)
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
    c = Cell(payload(1))
    saved = observe(c)  # tpyc: ok
    print("fixed", saved.value)


def external(flag: bool):
    owner = Cell(21)
    # The owner alias must not copy.
    other = Cell(22)
    saved = choose(flag, owner, other)  # tpyc: ok
    owner.value = 23
    print("external", saved.value)


def loops():
    remaining = 2
    while remaining > 0:
        # The backing lives through this iteration.
        c = Cell(remaining)
        saved = observe(c)  # tpyc: ok
        print("while", saved.value)
        remaining -= 1
    for index in range(2):
        c = Cell(index)
        item = observe(c)  # tpyc: ok
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
