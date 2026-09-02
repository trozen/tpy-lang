from tpy import Int32, Own, Ptr, StrView, nocopy
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def make() -> tuple[Int32, tuple[Int32, Int32]]:
    return (1, (2, 3))
def main() -> None:
    d: dict[Int32, tuple[Int32, tuple[Int32, Int32]]] = {}
    d[1] = make()
    print(len(d))
main()
