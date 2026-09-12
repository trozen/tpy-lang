from tpy import int32, Own, Ptr, StrView, char, String, Span
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def main() -> None:
    b = Box(1)
    c = Box(2)
    f = True
    t: tuple[int32, Box] = (1, b if f else c)
    print(t[0])
main()
