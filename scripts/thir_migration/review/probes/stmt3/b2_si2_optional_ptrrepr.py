from tpy import int32, Own, Ptr, StrView
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def main() -> None:
    xs: list[Box | None] = [None, None]
    b = Box(1)
    xs[0] = b
    xs[1] = None
    print(len(xs))
main()
