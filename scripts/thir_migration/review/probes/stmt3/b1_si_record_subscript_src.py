from tpy import Int32, Own, Ptr, StrView, nocopy
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def main() -> None:
    xs: list[Box] = [Box(1), Box(2)]
    ys: list[Box] = [Box(3), Box(4)]
    xs[0] = ys[1]
    print(xs[0].n)
main()
