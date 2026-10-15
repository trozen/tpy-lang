from tpy import int32
import b
from h import Handle


class Op:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __sub__(self, x: Handle[b.Item]) -> int32:  # tpyc: error(/parameter 'x' of 'Op.__sub__' in 'a' names 'Handle\[Item\]'/)
        return self.k


def run() -> int32:
    x = Handle[b.Item]()
    return Op(2) - x
