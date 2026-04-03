# Augmented assignment on property is not yet supported
from tpy import Int32

class Counter:
    _n: Int32

    def __init__(self) -> None:
        self._n = 0

    @property
    def n(self) -> Int32:
        return self._n

    @n.setter
    def n(self, v: Int32) -> None:
        self._n = v

def main() -> None:
    c = Counter()
    c.n += 1  # tpyc: error(/Augmented assignment on property/)

main()
