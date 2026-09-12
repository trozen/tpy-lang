from tpy import int32, Own, Ptr, StrView, char, String
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def find(xs: list[Box]) -> Box | None:
    return xs[0]
def a(xs: list[Box]) -> Box | None:
    return find(xs)
def main() -> None:
    xs = [Box(1)]
    print(1 if a(xs) is None else 0)
main()
