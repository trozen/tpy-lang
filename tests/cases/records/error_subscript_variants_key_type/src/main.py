# A subscript key no __getitem__ variant takes names every key type the
# variants declare, under the neutral label (their parameter names differ).
from tpy import int32, dispatch


class Table:
    def __init__(self) -> None:
        pass

    @dispatch
    def __getitem__(self, i: int32) -> int32:
        return i

    @dispatch
    def __getitem__(self, name: str) -> int32:
        return len(name)


def main() -> None:
    t = Table()
    print(t[1.5])  # tpyc: error(/Type mismatch in subscript key: expected int32 or str, got float/)


main()
