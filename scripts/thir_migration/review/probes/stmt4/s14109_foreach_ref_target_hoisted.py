from tpy import Int32
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def f(pairs: list[tuple[Box, Int32]]) -> Int32:
    t = Int32(0)
    for b, n in pairs:
        t += n
    return t + b.n
def main() -> None:
    print(f([(Box(1), 2)]))
main()
