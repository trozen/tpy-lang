from tpy import Int32, Own, Ptr, StrView, Char, Array
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def mk(b: Box) -> tuple[Box | None, Box | None]:
    return (b, None)
def main() -> None:
    d: dict[Int32, tuple[Box | None, Box | None]] = {}
    b = Box(1)
    t = mk(b)
    d[1] = t
    print(len(d))
main()
