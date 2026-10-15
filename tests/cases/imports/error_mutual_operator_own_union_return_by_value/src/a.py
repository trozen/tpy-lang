from tpy import Own, int32
import b


class Maker:
    def __add__(self, n: int32) -> Own[b.Item | b.Other]:  # tpyc: error(/Cyclic import: return type/)
        if n > 0:
            return b.Item(n)
        return b.Other(n)


def run() -> int32:
    r = Maker() + 2
    if isinstance(r, b.Item):
        return r.n
    return 0
