from tpy import Int32, Own, Ptr, StrView, Char, String, Span, Array
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def take(b: Own[Box]) -> Int32:
    return b.n
def a(b: Own[Box], f: bool) -> Int32:
    if f:
        c = b
        return take(c)
    return 0
def main() -> None:
    print(a(Box(1), True))
main()
