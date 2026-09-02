from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Rec2:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def cnt(self) -> Int32:
        return self.n
def main() -> None:
    r = Rec2(3)
    if r.cnt():
        print(1)
main()
