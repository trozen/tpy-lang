from tpy import int32, Own, Ptr, StrView, nocopy
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def main() -> None:
    b1 = Box(1)
    b2 = Box(2)
    d: dict[int32, tuple[Box, Box]] = {}
    d[1] = (b1, b2)
    print(len(d))
main()
