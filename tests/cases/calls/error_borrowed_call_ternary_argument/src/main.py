# Borrowed-call ternaries with constructor arguments await a whole-body lifetime proof.
# This unsupported form is documented in docs/LANGUAGE_FEATURES.md (borrowed-argument storage).
from tpy import int32, readonly


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def observe(cell: Cell) -> readonly[Cell]:
    return cell


def probe(flag: bool) -> int32:
    # Sema's statement-scoped lend-back model warns before lowering rejects.
    saved = observe(Cell(2)) if flag else observe(Cell(3))  # tpyc: warning(/Result borrows from temporary argument/) warning(/Result borrows from temporary argument/) error(/not yet supported/)
    return saved.value


def main():
    print(probe(True))


main()
