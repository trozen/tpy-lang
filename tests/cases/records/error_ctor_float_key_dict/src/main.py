# Dict keys keep the receiver-slice rule (fixed int / BigInt / owned str); a
# float key is outside it, so the constructor rejects.
from tpy import Int32


class Table:
    m: dict[float, Int32]

    def __init__(self) -> None:
        self.m = {1.5: 2}  # tpyc: error(/ctor.mil_field.container/)


def main() -> None:
    t = Table()
    print("built")


main()
