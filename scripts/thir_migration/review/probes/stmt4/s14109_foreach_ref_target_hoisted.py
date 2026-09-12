from tpy import int32
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def f(pairs: list[tuple[Box, int32]]) -> int32:
    t = int32(0)
    for b, n in pairs:
        t += n
    return t + b.n
def main() -> None:
    print(f([(Box(1), 2)]))
main()
