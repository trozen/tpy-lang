from tpy import Int32, Own, Ptr, StrView, Char, Array
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def a(d: dict[Int32, tuple[Int32, Box]]) -> tuple[Int32, Box]:
    return d[1]
def main() -> None:
    print(1)
main()
