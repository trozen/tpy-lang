from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def make(b: Rec) -> tuple[Int32, Rec]:
    return (1, b)
def f(b: Rec) -> Int32:
    return make(b)[0]
def main() -> None:
    print(f(Rec(1)))
main()
