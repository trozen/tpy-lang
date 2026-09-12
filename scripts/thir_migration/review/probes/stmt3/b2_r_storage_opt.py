from tpy import int32, Own, Ptr, StrView
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def a(xs: list[Box]) -> Own[Box] | None:
    return xs[0]
def b(n: int32) -> Own[Box] | None:
    return Box(n)
def main() -> None:
    xs = [Box(1)]
    r = a(xs)
    print(1 if r is None else 0)
    r2 = b(2)
    print(1 if r2 is None else 0)
main()
