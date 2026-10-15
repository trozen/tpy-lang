from tpy import Span, int32
import b


class Summer:
    def __init__(self, k: int32) -> None:
        self.k = k

    def total[T](self, k: T, xs: Span[b.Val]) -> int32:  # tpyc: error(/Cyclic import: parameter 'xs'/)
        t = self.k
        for v in xs:
            t += v.n
        return t


def run() -> int32:
    vals = [b.Val(1), b.Val(2)]
    return Summer(1).total(1, vals)
