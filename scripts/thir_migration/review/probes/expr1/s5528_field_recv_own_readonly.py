from tpy import Int32, Own, readonly
class Cell:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Box:
    inner: Cell
    def __init__(self, n: Int32) -> None:
        self.inner = Cell(n)
def read_field(o: Own[readonly[Box]]) -> Int32:
    return o.inner.n
def main() -> None:
    print(read_field(Box(2)))
main()
