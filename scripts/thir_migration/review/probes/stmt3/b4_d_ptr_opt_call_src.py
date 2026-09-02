from tpy import Int32, Own, Ptr, StrView, Char, String
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def find(xs: list[Box]) -> Box | None:
    return xs[0]
def a(xs: list[Box]) -> Box | None:
    return find(xs)
def main() -> None:
    xs = [Box(1)]
    print(1 if a(xs) is None else 0)
main()
