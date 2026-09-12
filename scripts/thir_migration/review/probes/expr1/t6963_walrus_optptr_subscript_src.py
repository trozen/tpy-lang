from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def f(xs: list[Rec | None]) -> int32:
    if (r := xs[0]) is not None:
        return r.n
    return 0
def main() -> None:
    pass
main()
