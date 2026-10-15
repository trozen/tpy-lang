from tpy import int32
import b


class Neg:
    def __init__(self, k: int32) -> None:
        self.k = k

    # The friend `operator-` returns `Val` by value, which `a.hpp` sees only
    # forward-declared.
    def __neg__(self) -> b.Val:  # tpyc: error(/Cyclic import: return type/)
        return b.Val(-self.k)


def run() -> int32:
    v = -Neg(2)
    return v.n
