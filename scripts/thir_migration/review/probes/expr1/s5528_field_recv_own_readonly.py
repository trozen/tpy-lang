from tpy import int32, Own, readonly
class Cell:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Box:
    inner: Cell
    def __init__(self, n: int32) -> None:
        self.inner = Cell(n)
def read_field(o: Own[readonly[Box]]) -> int32:
    return o.inner.n
def main() -> None:
    print(read_field(Box(2)))
main()
