from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
G = Rec(3)
def f() -> Int32:
    if (q := G).n > 0:
        return q.n
    return 0
def main() -> None:
    pass
main()
