from tpy import int32
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def f(pairs: list[tuple[int32, Box]]) -> int32:
    a, b = pairs[0]
    return a + b.n
def main() -> None:
    print(f([(1, Box(2))]))
main()
