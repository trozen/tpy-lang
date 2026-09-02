from tpy import Int32, Own, Ptr, StrView
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def a(f: bool) -> Own[tuple[str, Box]]:
    t = ('x', Box(1))
    u = ('y', Box(2))
    return t if f else u
def main() -> None:
    print(a(True)[0])
main()
