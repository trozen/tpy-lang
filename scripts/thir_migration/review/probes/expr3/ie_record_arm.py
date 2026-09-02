from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def main() -> None:
    c = True
    rs = [Rec(1), Rec(2)]
    r = rs[0] if c else rs[1]
    print(r.n)
main()
