# A `with` target read and rebound by ONE statement (`g = g.next()`). The rebind
# does not kill the read that feeds it, so the manager must outlive its branch
# for the read to be valid -- treating the statement as a pure rebind drops the
# manager first and reads freed storage.
from tpy import int32, Own


class Reg:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        self.n = -999

    def next(self) -> Own["Reg"]:
        return Reg(self.n + 1)


def probe(flag: bool) -> int32:
    if flag:
        with Reg(11) as g:
            pass
    else:
        with Reg(22) as g:
            pass

    g = g.next()  # the read of the old `g` happens before the rebind
    return g.n


def main() -> None:
    print(probe(True))
    print(probe(False))


main()
