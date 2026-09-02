from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
from tpy import readonly
class H:
    pair: tuple[Int32, Rec]
    def __init__(self) -> None:
        self.pair = (1, Rec(2))
def mk(b: Rec) -> tuple[Int32, Rec]:
    return (1, b)
def f(b: Rec, h: readonly[H]) -> Int32:
    if (t := mk(b))[0] > 0:
        return t[1].n
    t = h.pair
    return t[1].n
def main() -> None:
    pass
main()
