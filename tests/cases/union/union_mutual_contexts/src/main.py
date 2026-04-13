# Mutual + self-referencing recursive unions in the same module,
# with isinstance narrowing, multiple cycle groups, and implicit __init__
from tplib import Box
from tpy import Own

# D20: mutual recursion (Expr <-> BinOp)
type Expr = Lit | BinOp

class Lit:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

class BinOp:
    left: Box[Expr]
    right: Box[Expr]
    def __init__(self, left: Own[Box[Expr]], right: Own[Box[Expr]]) -> None:
        self.left = left
        self.right = right

# D19: self-referencing union (same module)
type Tree = int | list[Tree]

# Second independent D20 cycle group
type Value = int | str | Neg

class Neg:
    inner: Box[Value]
    def __init__(self, inner: Own[Box[Value]]) -> None:
        self.inner = inner

# isinstance narrowing on mutual recursive union
def describe_expr(e: Expr) -> str:
    if isinstance(e, Lit):
        return str(e.value)
    elif isinstance(e, BinOp):
        return "(" + describe_expr(e.left.get()) + "+" + describe_expr(e.right.get()) + ")"
    else:
        return "?"

# isinstance narrowing on second cycle group (including Neg construction)
def show_value(v: Value) -> str:
    if isinstance(v, Neg):
        return "-" + show_value(v.inner.get())
    elif isinstance(v, int):
        return str(v)
    else:
        return v

def main() -> None:
    # D20 isinstance narrowing
    print(describe_expr(Lit(1)))
    e = BinOp(Box(Lit(2)), Box(Lit(3)))
    print(describe_expr(e))

    # D19 self-referencing in same module
    t: Tree = [1, [2, 3]]
    print(t)

    # Second cycle group: construct Neg (implicit __init__)
    n = Neg(Box(42))
    print(show_value(n))
    print(show_value(7))

main()
