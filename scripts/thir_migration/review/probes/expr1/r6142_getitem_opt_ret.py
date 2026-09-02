from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Grid:
    cells: list[Rec]
    def __init__(self) -> None:
        self.cells = [Rec(1)]
    def __getitem__(self, i: Int32) -> Int32 | None:
        return self.cells[i].n
def f(g: Grid) -> Int32:
    v = g[0]
    return 0 if v is None else v
def main() -> None:
    pass
main()
