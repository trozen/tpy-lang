# A generator expression unpacking tuples out of a LIST LITERAL of record
# elements: the source is consumed per element, which the unpack binding has no
# arm for. This is rejected: TPy does not compile this shape today.
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    total = sum(n for p, n in [(P(1), 10)])  # tpyc: error(/genexpr\.unpack/)
    print(total)


main()
