# The adjacent shape the field-write rows must keep rejecting: an
# OPTIONAL bytearray field written from a converting construction. Its bare
# sibling routes; the Optional slot needs a lift no source row here spells.
from tpy import Int32


class Buf:
    n: Int32
    ba: bytearray | None

    def __init__(self, k: Int32) -> None:
        self.n = k
        if k < 0:
            raise ValueError("neg")
        self.ba = None

    def load(self, src: bytes) -> None:
        self.ba = bytearray(src)  # tpyc: error(/not yet supported/)


def main() -> None:
    b = Buf(1)
    b.load(b"ab")


main()
