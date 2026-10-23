# An augmented subscript store on a type with __getitem__ but no __setitem__
# is refused as a missing item assignment.
from tpy import int32


class ReadOnlyTable:
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: int32) -> int32:
        return k


def main() -> None:
    t = ReadOnlyTable()
    t[0] += 1  # tpyc: error(/Type 'ReadOnlyTable' does not support item assignment/)
    print(t[0])


main()
