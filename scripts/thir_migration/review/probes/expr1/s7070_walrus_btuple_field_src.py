from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class H:
    pair: tuple[int32, Rec]
    def __init__(self) -> None:
        self.pair = (1, Rec(2))
def f(h: H) -> int32:
    if (t := h.pair)[0] > 0:
        return t[1].n
    return 0
def main() -> None:
    pass
main()
