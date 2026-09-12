# Augmented assignment on property is not yet supported
from tpy import int32

class Counter:
    _n: int32

    def __init__(self) -> None:
        self._n = 0

    @property
    def n(self) -> int32:
        return self._n

    @n.setter
    def n(self, v: int32) -> None:
        self._n = v

def main() -> None:
    c = Counter()
    c.n += 1  # tpyc: error(/Augmented assignment on property/)

main()
