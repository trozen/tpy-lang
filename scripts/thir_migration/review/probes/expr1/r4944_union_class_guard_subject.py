from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Other:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
def f(v: Rec | Other) -> int32:
    match v:
        case Rec(n=1) if v.n > 0:
            return 1
        case Rec(n=k) if k > 0:
            return k
        case _:
            return 0
def main() -> None:
    pass
main()
