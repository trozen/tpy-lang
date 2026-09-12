from tpy import int32, Own, Ptr, StrView, char, String, Span, Array
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def take(b: Own[Box]) -> int32:
    return b.n
def a(b: Own[Box], f: bool) -> int32:
    if f:
        c = b
        return take(c)
    return 0
def main() -> None:
    print(a(Box(1), True))
main()
