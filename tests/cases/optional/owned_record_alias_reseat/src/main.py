# Optional copies share the original record; replacing or clearing one holder
# must preserve aliases, including inside methods and constructor tails.
from tpy import int32


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def free_reseat(flag: bool) -> None:
    current: Cell | None = Cell(1)
    saved: Cell | None = current  # tpyc: ok
    # Mutating before replacement distinguishes an alias from a silent copy.
    if current is not None:
        current.value = 7
    if saved is not None:
        print("free shared:", saved.value)
    # Each branch replaces only current; saved retains the original storage.
    if flag:
        current = Cell(2)  # tpyc: ok
    else:
        current = Cell(3)  # tpyc: ok
    if saved is not None:
        saved.value = 9
    if current is not None:
        print("free replacement:", current.value)
    current = None  # tpyc: ok
    if saved is not None:
        print("free cleared:", saved.value, current is None)


class Host:
    value: int32

    def __init__(self):
        self.value = 0
        current: Cell | None = Cell(1)
        saved: Cell | None = current
        # Constructor-tail aliases observe mutation and survive replacement.
        if current is not None:
            current.value = 7
        current = Cell(2)  # tpyc: ok
        if saved is not None:
            self.value = saved.value

    def retained(self) -> int32:
        current: Cell | None = Cell(3)
        saved: Cell | None = current
        # Method-local Optional copies keep the referent after None clears current.
        if current is not None:
            current.value = 8
        current = None  # tpyc: ok
        if saved is not None:
            return saved.value
        return 0


def main() -> None:
    free_reseat(False)
    free_reseat(True)
    host = Host()
    print("constructor:", host.value)
    print("method:", host.retained())


main()
