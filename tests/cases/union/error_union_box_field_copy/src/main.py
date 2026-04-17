# Error: assigning a non-Own Box param to a Box field would delete-copy in C++
from tplib import Box

type Expr = Lit | BinOp

class Lit:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

class BinOp:
    left: Box[Expr]
    right: Box[Expr]
    def __init__(self, left: Box[Expr], right: Box[Expr]) -> None:
        self.left = left  # tpyc: error(/non-copyable/)
        self.right = right
