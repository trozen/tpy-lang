# Error: mutual recursion without indirection (infinite size)
type Expr = Lit | BinOp  # tpyc: error(/infinite-size cycle/)

class Lit:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

class BinOp:
    left: Expr
    op: str
    right: Expr
