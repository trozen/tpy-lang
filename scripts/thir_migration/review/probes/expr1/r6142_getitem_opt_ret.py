from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Grid:
    cells: list[Rec]
    def __init__(self) -> None:
        self.cells = [Rec(1)]
    def __getitem__(self, i: int32) -> int32 | None:
        return self.cells[i].n
def f(g: Grid) -> int32:
    v = g[0]
    return 0 if v is None else v
def main() -> None:
    pass
main()
