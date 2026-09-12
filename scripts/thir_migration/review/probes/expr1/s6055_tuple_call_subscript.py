from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def make(b: Rec) -> tuple[int32, Rec]:
    return (1, b)
def f(b: Rec) -> int32:
    return make(b)[0]
def main() -> None:
    print(f(Rec(1)))
main()
