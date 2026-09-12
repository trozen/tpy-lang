from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Rec2:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def cnt(self) -> int32:
        return self.n
def main() -> None:
    r = Rec2(3)
    if r.cnt():
        print(1)
main()
