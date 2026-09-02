from tpy import Int32, Own
class F:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def make(self) -> Own[list[Int32]]:
        return [self.n, self.n]
def go(f: F) -> None:
    a: list[Int32] | None = [1, 2]
    if a is not None:
        print(len(a))
    a = f.make()
    if a is not None:
        print(len(a))
def main() -> None:
    go(F(3))
main()
