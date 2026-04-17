# D20 mutual recursion with classes defined BEFORE the union alias.
# Exercises the codegen path where Box[Expr] fields carry NamedType("Expr")
# (alias not yet registered at parse time) rather than the expanded UnionType.
from tplib import Box
from tpy import Own

class Lit:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

class BinOp:
    left: Box[Expr]
    op: str
    right: Box[Expr]
    def __init__(self, left: Own[Box[Expr]], op: str, right: Own[Box[Expr]]) -> None:
        self.left = left
        self.op = op
        self.right = right

type Expr = Lit | BinOp

def eval_expr(e: Expr) -> int:
    match e:
        case Lit(value=v):
            return v
        case BinOp(left=l, op=op, right=r):
            lv = eval_expr(l.get())
            rv = eval_expr(r.get())
            if op == "+":
                return lv + rv
            else:
                return lv - rv

def main() -> None:
    print(eval_expr(Lit(42)))
    e = BinOp(Box(BinOp(Box(Lit(10)), "+", Box(Lit(3)))), "-", Box(Lit(1)))
    print(eval_expr(e))

main()
