from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def f(b: Rec, c: Rec) -> int32:
    if (t := (1, b))[0] > 0:
        return t[1].n
    t = (2, c)
    return t[1].n
def main() -> None:
    pass
main()
