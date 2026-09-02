from tpy import Int32
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def f(pairs: list[tuple[Int32, Box]]) -> Int32:
    a, b = pairs[0]
    return a + b.n
def main() -> None:
    print(f([(1, Box(2))]))
main()
