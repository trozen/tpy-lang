from tpy import Int32, Own, Ptr, StrView, Char, String, Span
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def main() -> None:
    b = Box(1)
    c = Box(2)
    f = True
    t: tuple[Int32, Box] = (1, b if f else c)
    print(t[0])
main()
