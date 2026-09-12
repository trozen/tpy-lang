from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
G = Rec(3)
def f() -> int32:
    if (q := G).n > 0:
        return q.n
    return 0
def main() -> None:
    pass
main()
