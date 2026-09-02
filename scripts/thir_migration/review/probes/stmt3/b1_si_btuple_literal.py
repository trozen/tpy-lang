from tpy import Int32, Own, Ptr, StrView, nocopy
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def main() -> None:
    b1 = Box(1)
    b2 = Box(2)
    d: dict[Int32, tuple[Box, Box]] = {}
    d[1] = (b1, b2)
    print(len(d))
main()
