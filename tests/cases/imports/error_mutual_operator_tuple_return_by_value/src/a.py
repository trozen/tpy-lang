from tpy import int32
import b


class Pairer:
    def __init__(self, k: int32) -> None:
        self.k = k

    def __mul__(self, n: int32) -> tuple[b.Val, int32]:  # tpyc: error(/Cyclic import: return type/)
        return (b.Val(n), self.k)


def run() -> int32:
    v, k = Pairer(2) * 3
    return v.n + k
