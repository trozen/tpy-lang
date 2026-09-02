from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def f(xs: list[Rec | None]) -> Int32:
    if (r := xs[0]) is not None:
        return r.n
    return 0
def main() -> None:
    pass
main()
