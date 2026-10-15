from tpy import int32
import b


class A:
    x: b.Item | None  # tpyc: error(/Cyclic import: field 'A.x' stores 'Item'/)

    def __init__(self, x: b.Item | None) -> None:
        self.x = x


def run() -> int32:
    return 0 if A(b.Item(1)).x is None else 1
