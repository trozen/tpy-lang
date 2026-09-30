# Dict keys keep the receiver-slice rule (fixed int / BigInt / owned str); a
# float key is outside it, so the constructor rejects.
from tpy import int32


class Table:
    m: dict[float, int32]

    def __init__(self) -> None:
        self.m = {1.5: 2}  # tpyc: error(/assign\.field_write_shape/)


def main() -> None:
    t = Table()
    print("built")


main()
